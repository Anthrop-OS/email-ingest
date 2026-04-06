import imaplib
import email
from email.message import Message
from typing import List, Dict, Any, Callable, Tuple, Optional
from bs4 import BeautifulSoup
from core.config_loader import EmailAccountConfig
from core.persistence import PersistenceManager
import logging

logger = logging.getLogger(__name__)

class EmailFetcher:
    def __init__(self, account_config: EmailAccountConfig, persistence: PersistenceManager, is_dry_run: bool = False):
        self.account = account_config
        self.persistence = persistence
        self.dry_run = is_dry_run

    def fetch_new_emails(self, start_uid: int, since_date: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
        from typing import Tuple
        from datetime import datetime
        password = self.account.get_password()
        
        if self.account.use_ssl:
            mail = imaplib.IMAP4_SSL(self.account.imap_server, self.account.imap_port)
        else:
            mail = imaplib.IMAP4(self.account.imap_server, self.account.imap_port)
            
        try:
            mail.login(self.account.username, password)
            # TODO(P1): Check UIDVALIDITY from mail.select() response.
            # If UIDVALIDITY has changed since our last recorded value, all UIDs
            # have been reassigned. We must invalidate the cursor to prevent
            # processing the wrong emails or missing newly assigned UIDs.
            # Store UIDVALIDITY in account_cursors table and compare on each run.
            mail.select(self.account.fetch_folder)
            
            logger.info(f"Fetching for {self.account.account_id} since UID: {start_uid - 1}")
            
            search_query = f'UID {start_uid}:*'
            if since_date:
                try:
                    dt = datetime.strptime(since_date, "%Y-%m-%d")
                    imap_date = dt.strftime("%d-%b-%Y") # 01-Jan-2024
                    search_query += f' SINCE {imap_date}'
                except ValueError:
                    logger.error(f"Invalid date format: {since_date}. Expected YYYY-MM-DD.")
                    return [], start_uid - 1
            
            status, response = mail.uid('SEARCH', None, search_query)
            
            if status != 'OK':
                logger.error(f"Failed to search for new emails: {status}")
                return [], start_uid - 1
                
            uids_str = response[0].decode('utf-8').strip()
            if not uids_str:
                return [], start_uid - 1
                
            uids = [int(u) for u in uids_str.split()]
            uids = [u for u in uids if u >= start_uid]
            
            if not uids:
                return [], start_uid - 1

            if self.dry_run:
                logger.warning(f"[DRY-RUN] Will not fetch {len(uids)} emails. Identified UIDs: {uids}")
                max_uid = max(uids) if uids else start_uid - 1
                return [], max_uid

            fetched_emails = []
            max_uid_seen = start_uid - 1

            for uid in uids:
                status, fetch_data = mail.uid('FETCH', str(uid), '(RFC822)')
                if status == 'OK':
                    # Parse the RFC822 response payload correctly
                    for response_part in fetch_data:
                        if isinstance(response_part, tuple):
                            raw_email = response_part[1]
                            msg = email.message_from_bytes(raw_email)
                            
                            email_data = {
                                "uid": uid,
                                "account_id": self.account.account_id,
                                "subject": self._decode_header(msg.get("Subject", "")),
                                "sender": msg.get("From", ""),
                                "date": msg.get("Date", ""),
                                "body": self._extract_body(msg)
                            }
                            fetched_emails.append(email_data)
                            max_uid_seen = max(max_uid_seen, uid)

            if fetched_emails:
                logger.info(f"Fetch complete: {len(fetched_emails)} emails found, UID range {min(e['uid'] for e in fetched_emails)}~{max(e['uid'] for e in fetched_emails)}")
            else:
                logger.info("Fetch complete: 0 emails found")

            # Idempotency is preserved. The caller updates the cursor.
            return fetched_emails, max_uid_seen

        finally:
            try:
                mail.logout()
            except:
                pass
                
    def _decode_header(self, raw_header: str) -> str:
        from email.header import decode_header
        if not raw_header:
            return ""
        decoded_parts = []
        for part, encoding in decode_header(raw_header):
            if isinstance(part, bytes):
                decoded_parts.append(part.decode(encoding or 'utf-8', errors='ignore'))
            else:
                decoded_parts.append(str(part))
        return "".join(decoded_parts)
        
    def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text using BeautifulSoup."""
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            link_text = a_tag.get_text()
            if link_text.strip():
                a_tag.replace_with(f"{link_text} ({href})")
            else:
                a_tag.replace_with(href)
        return soup.get_text(separator="\n", strip=True)

    def _extract_body(self, msg: Message) -> str:
        plain_body = ""
        html_body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))
                if "attachment" in content_disposition:
                    continue
                try:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    decoded = payload.decode(errors='ignore')
                except Exception:
                    continue
                if content_type == "text/plain" and not plain_body:
                    plain_body = decoded
                elif content_type == "text/html" and not html_body:
                    html_body = decoded
        else:
            content_type = msg.get_content_type()
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    decoded = payload.decode(errors='ignore')
                    if content_type == "text/html":
                        html_body = decoded
                    else:
                        plain_body = decoded
            except Exception:
                plain_body = str(msg.get_payload())

        if plain_body.strip():
            return plain_body
        if html_body.strip():
            return self._html_to_text(html_body)
        return ""
