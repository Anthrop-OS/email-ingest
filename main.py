import os
import sys
import argparse
import logging
import json
from pathlib import Path
from filelock import FileLock, Timeout

from core.config_loader import ConfigLoader
from core.persistence import PersistenceManager
from core.content_hasher import compute_email_fingerprint
from modules.email_fetcher import EmailFetcher
from modules.nlp_processor import NLPProcessor, LLMResponse
from modules.output_channel import ConsoleOutputChannel, FileOutputChannel

# Default logger setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

def parse_args():
    parser = argparse.ArgumentParser(description="Daemon-less Email Ingest & NLP Triage Processor")
    parser.add_argument("--config", default="config.yaml", help="Path to the config.yaml file")
    parser.add_argument("--dry-run", action="store_true", help="Run without network calls or DB side-effects")
    parser.add_argument("--target-account", help="Execute only for a specific account email")
    parser.add_argument("--format", choices=["console", "json"], default="console", help="Render format")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Override level")
    parser.add_argument("--reset-cursor", action="store_true", help="DANGER: Resets cursor to 0")
    parser.add_argument("--force-from-uid", type=int, help="Override SQLite UID cursor and start from this UID")
    parser.add_argument("--init-start-date", help="Format YYYY-MM-DD. Mandatory on first run to prevent avalanche.")
    parser.add_argument("--skip-nlp", action="store_true", help="Bypass LLM")
    parser.add_argument("--force-reprocess", action="store_true", help="Ignore NLP cache and re-run LLM for all emails in this run")
    parser.add_argument("--output-file", help="JSON file path to safely dump processed results")
    return parser.parse_args()

def main():
    args = parse_args()
    if args.log_level:
        logging.getLogger().setLevel(getattr(logging, args.log_level))
        
    logger.debug(f"Starting execution with args: {args}")

    if not Path(args.config).exists():
        logger.error(f"Configuration file {args.config} not found.")
        sys.exit(1)
        
    config = ConfigLoader.load(args.config)
    persistence = PersistenceManager(config.settings.db_path)
    
    if args.output_file:
        output_channel = FileOutputChannel(args.output_file)
    else:
        tmpl_name = "console_output.j2" if args.format == "console" else "NON_EXISTENT_FORCE_JSON"
        output_channel = ConsoleOutputChannel(template_name=tmpl_name)

    accounts_to_process = config.email_accounts
    if args.target_account:
        accounts_to_process = [acc for acc in accounts_to_process if acc.account_id == args.target_account]
        if not accounts_to_process:
            logger.error(f"Account {args.target_account} not found in {args.config}")
            sys.exit(1)

    overall_success = True

    for account in accounts_to_process:
        logger.info(f"== Processing Account: {account.account_id} ==")
        
        # P1: Overlapping Crons Protection
        lock_path = Path(config.settings.db_path).parent / f"{account.account_id}.lock"
        try:
            lock = FileLock(str(lock_path), timeout=0)
            lock.acquire()
        except Timeout:
            logger.warning(f"Cron collision: {account.account_id} is locked by another process. Skipping.")
            continue
            
        try:
            if args.reset_cursor and not args.dry_run:
                logger.warning(f"Resetting cursor for {account.account_id} to 0")
                persistence.update_cursor(account.account_id, 0)

            start_uid = 1
            if args.force_from_uid is not None:
                logger.warning(f"Forcing execution to start from UID {args.force_from_uid}")
                start_uid = args.force_from_uid
            else:
                start_uid = persistence.get_cursor(account.account_id) + 1

            # P0: Avalanche Protection
            if start_uid == 1 and not args.force_from_uid and not args.init_start_date:
                logger.error(f"Initial run detected for {account.account_id}! You must specify --init-start-date YYYY-MM-DD to prevent Token Avalanche.")
                overall_success = False
                continue

            fetcher = EmailFetcher(account, persistence, is_dry_run=args.dry_run)
            try:
                emails_data, highest_fetched_uid = fetcher.fetch_new_emails(start_uid, since_date=args.init_start_date)
            except Exception as e:
                logger.error(f"Fetcher failed for {account.account_id}: {e}")
                overall_success = False
                continue

            if not emails_data:
                logger.info("No new emails found.")
                if not args.dry_run:
                    persistence.log_audit(account.account_id, start_uid - 1, start_uid - 1, 0, "SUCCESS", "No new emails")
                continue

            processed_results = []
            
            if args.skip_nlp:
                logger.info("Skipping NLP processing (--skip-nlp enabled).")
                processed_results = [
                    {
                        "original_uid": e.get("uid"),
                        "priority": "Unprocessed",
                        "summary": f"RAW: {e.get('subject')}",
                        "key_entities": [],
                        "action_required": False,
                        "is_truncated": False
                    } for e in emails_data
                ]
            else:
                nlp = NLPProcessor(
                    config.llm_provider,
                    persistence,
                    is_dry_run=args.dry_run,
                    force_reprocess=args.force_reprocess
                )
                for email_data in emails_data:
                    content_hash = compute_email_fingerprint(email_data)
                    try:
                        result = nlp.process_email(email_data, content_hash)
                        processed_results.append(result.model_dump())
                    except Exception as e:
                        logger.error(f"NLP skipping email UID {email_data.get('uid')} due to error: {e}")
                        # P1: Poison Pill Quarantine Fallback
                        fallback = LLMResponse(
                            original_uid=email_data.get('uid') or 0,
                            priority="Error",
                            summary=f"🔥 [NLP FAULT] NLP parsing crashed: {str(e)[:150]}",
                            key_entities=["NLP_CRITICAL_FAILURE", "QUARANTINE_WARNING"],
                            action_required=True,
                            is_truncated=True
                        )
                        processed_results.append(fallback.model_dump())
                        # Note: We do NOT set overall_success = False here anymore! Cursor is allowed to pass!

            if not processed_results:
                continue

            emit_success = output_channel.emit(account.account_id, processed_results)
            
            if emit_success and not args.dry_run:
                if highest_fetched_uid >= start_uid:
                    persistence.update_cursor(account.account_id, highest_fetched_uid)
                    logger.info(f"Cursor advanced for {account.account_id} to {highest_fetched_uid}")
                    
                persistence.log_audit(
                    account_id=account.account_id,
                    before_uid=start_uid - 1,
                    after_uid=highest_fetched_uid,
                    emails_processed=len(processed_results),
                    status="SUCCESS",
                    error_msg=""
                )
            elif not emit_success:
                logger.error("Emission failed! Aborting cursor update to maintain idempotency.")
                overall_success = False
                if not args.dry_run:
                    persistence.log_audit(
                        account_id=account.account_id,
                        before_uid=start_uid - 1,
                        after_uid=start_uid - 1,
                        emails_processed=0,
                        status="FAIL",
                        error_msg="Output emit failed"
                    )
        finally:
            lock.release()

    if not overall_success:
        logger.warning("Pipeline completed with partial errors.")
        sys.exit(1)
        
    logger.info("Email Ingestion Pipeline completed successfully.")
    sys.exit(0)

if __name__ == "__main__":
    main()

