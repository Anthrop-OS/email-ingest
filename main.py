import os
import sys
import argparse
import logging
import json
from pathlib import Path

from core.config_loader import ConfigLoader
from core.persistence import PersistenceManager
from modules.email_fetcher import EmailFetcher
from modules.nlp_processor import NLPProcessor
from modules.output_channel import ConsoleOutputChannel, FileOutputChannel

# Default logger setup (can be overridden by args)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

def parse_args():
    parser = argparse.ArgumentParser(description="Daemon-less Email Ingest & NLP Triage Processor")
    parser.add_argument("--config", default="config.yaml", help="Path to the config.yaml file")
    parser.add_argument("--dry-run", action="store_true", help="Run without network calls or DB side-effects")
    parser.add_argument("--target-account", help="Execute only for a specific account email")
    parser.add_argument("--format", choices=["console", "json"], default="console", help="Render format for stdout output")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Override environment log level")
    parser.add_argument("--reset-cursor", action="store_true", help="DANGER: Resets cursor for the target accounts to 0")
    parser.add_argument("--force-from-uid", type=int, help="Override SQLite UID cursor and start from this UID")
    parser.add_argument("--skip-nlp", action="store_true", help="Bypass LLM, pushing raw email dumps to output channel")
    parser.add_argument("--output-file", help="JSON file path to safely dump processed results to, bypassing stdout pollution")
    
    return parser.parse_args()

def main():
    args = parse_args()
    
    # Override log level dynamically
    if args.log_level:
        logging.getLogger().setLevel(getattr(logging, args.log_level))
        
    logger.debug(f"Starting execution with args: {args}")

    # 1. Config Loading
    if not Path(args.config).exists():
        logger.error(f"Configuration file {args.config} not found.")
        sys.exit(1)
        
    config = ConfigLoader.load(args.config)
    
    # 2. Persistence init
    persistence = PersistenceManager(config.settings.db_path)
    
    # 3. Output Channel Selection
    if args.output_file:
        output_channel = FileOutputChannel(args.output_file)
    else:
        # If --format is json, we pass an invalid template name so it falls back to raw json printing
        tmpl_name = "console_output.j2" if args.format == "console" else "NON_EXISTENT_FORCE_JSON"
        output_channel = ConsoleOutputChannel(template_name=tmpl_name)

    # 4. Filter accounts if targeted
    accounts_to_process = config.email_accounts
    if args.target_account:
        accounts_to_process = [acc for acc in accounts_to_process if acc.account_id == args.target_account]
        if not accounts_to_process:
            logger.error(f"Account {args.target_account} not found in {args.config}")
            sys.exit(1)

    overall_success = True

    for account in accounts_to_process:
        logger.info(f"== Processing Account: {account.account_id} ==")
        
        # Admin Override: Reset Cursor
        if args.reset_cursor and not args.dry_run:
            logger.warning(f"Resetting cursor for {account.account_id} to 0")
            persistence.update_cursor(account.account_id, 0)

        # 5. Determine Starting UID
        start_uid = 1
        if args.force_from_uid is not None:
            logger.warning(f"Forcing execution to start from UID {args.force_from_uid}")
            start_uid = args.force_from_uid
        else:
            start_uid = persistence.get_cursor(account.account_id) + 1

        # 6. Fetch Emails
        fetcher = EmailFetcher(account, persistence, is_dry_run=args.dry_run)
        try:
            emails_data, highest_fetched_uid = fetcher.fetch_new_emails(start_uid)
        except Exception as e:
            logger.error(f"Fetcher failed for {account.account_id}: {e}")
            overall_success = False
            continue

        if not emails_data:
            logger.info("No new emails found.")
            # Record audit log anyway
            if not args.dry_run:
                persistence.log_audit(account.account_id, start_uid - 1, start_uid - 1, 0, "SUCCESS", "No new emails")
            continue

        processed_results = []
        
        # 7. NLP Processing
        if args.skip_nlp:
            logger.info("Skipping NLP processing (--skip-nlp enabled).")
            # Convert raw payload directly for JSON dumping structure
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
            nlp = NLPProcessor(config.llm_provider, is_dry_run=args.dry_run)
            for email_data in emails_data:
                try:
                    result = nlp.process_email(email_data)
                    # Convert Pydantic object to dict for output channel
                    processed_results.append(result.model_dump())
                except Exception as e:
                    logger.error(f"NLP skipping email UID {email_data.get('uid')} due to error: {e}")
                    overall_success = False

        if not processed_results:
            continue

        # 8. Output emit & Idempotent persistence update
        emit_success = output_channel.emit(account.account_id, processed_results)
        
        if emit_success and not args.dry_run:
            # Commit the progress permanently!
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

    if not overall_success:
        logger.warning("Pipeline completed with partial errors.")
        sys.exit(1)
        
    logger.info("Email Ingestion Pipeline completed successfully.")
    sys.exit(0)

if __name__ == "__main__":
    main()
