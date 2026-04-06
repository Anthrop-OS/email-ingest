import os
import sys
import argparse
import logging
import json
import time as _time
from uuid import uuid4
from pathlib import Path
from filelock import FileLock, Timeout

from core.config_loader import ConfigLoader
from core.persistence import PersistenceManager
from core.content_hasher import compute_email_fingerprint
from modules.email_fetcher import EmailFetcher
from modules.nlp_processor import NLPProcessor, LLMResponse
from modules.output_channel import ConsoleOutputChannel, FileOutputChannel

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
    run_id = uuid4().hex[:8]
    log_level = getattr(logging, args.log_level) if args.log_level else logging.INFO
    logging.basicConfig(
        level=log_level,
        format=f"%(asctime)s [%(levelname)s] [{run_id}] %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    t_start = _time.monotonic()
    total_fetched = 0
    total_llm_calls = 0
    total_cache_hits = 0
    total_errors = 0

    logger.info(
        f"Pipeline started | run_id={run_id} dry_run={args.dry_run} "
        f"format={args.format} init_date={args.init_start_date or 'N/A'} "
        f"skip_nlp={args.skip_nlp}"
    )

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

    for acct_idx, account in enumerate(accounts_to_process, 1):
        logger.info(f"== [{acct_idx}/{len(accounts_to_process)}] Processing Account: {account.account_id} ==")
        
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

            total_fetched += len(emails_data)

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
                for i, email_data in enumerate(emails_data, 1):
                    uid = email_data.get('uid')
                    logger.info(f"[{i}/{len(emails_data)}] Processing UID {uid} ...")
                    content_hash = compute_email_fingerprint(email_data)
                    try:
                        result = nlp.process_email(email_data, content_hash)
                        cache_hit = nlp.last_cache_hit
                        logger.info(f"[{i}/{len(emails_data)}] UID {uid} -> {result.priority} (cache_hit={cache_hit})")
                        if cache_hit:
                            total_cache_hits += 1
                        else:
                            total_llm_calls += 1
                        processed_results.append(result.model_dump())
                    except Exception as e:
                        total_errors += 1
                        logger.error(f"NLP skipping email UID {uid} due to error: {e}")
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
                    logger.info(f"Cursor advanced for {account.account_id} from {start_uid - 1} -> {highest_fetched_uid} (+{highest_fetched_uid - start_uid + 1} emails)")
                    
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

    elapsed = _time.monotonic() - t_start
    mins, secs = divmod(int(elapsed), 60)
    logger.info(
        f"\n{'=' * 28}\n  Pipeline Summary\n{'=' * 28}\n"
        f"  Accounts processed: {len(accounts_to_process)}\n"
        f"  Emails fetched:     {total_fetched}\n"
        f"  LLM calls made:     {total_llm_calls}  (cache hits: {total_cache_hits})\n"
        f"  Errors / Quarantine: {total_errors}\n"
        f"  Elapsed:            {mins}m {secs}s\n"
        f"{'=' * 28}"
    )

    if not overall_success:
        logger.warning("Pipeline completed with partial errors.")
        sys.exit(1)

    logger.info("Email Ingestion Pipeline completed successfully.")
    sys.exit(0)

if __name__ == "__main__":
    main()

