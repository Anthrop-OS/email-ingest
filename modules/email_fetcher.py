import imaplib
import email
from email.message import Message
from typing import List, Dict, Any, Callable
from core.config_loader import EmailAccountConfig
from core.persistence import PersistenceManager
import logging

logger = logging.getLogger(__name__)

class EmailFetcher:
    def __init__(self, account_config: EmailAccountConfig, persistence: PersistenceManager, is_dry_run: bool = False):
        self.account = account_config
        self.persistence = persistence
        self.dry_run = is_dry_run

    def fetch_new_emails(self, password_resolver: Callable[[], str]) -> List[Dict[str, Any]]:
        password = password_resolver()
        
        if self.account.use_ssl:
            mail = imaplib.IMAP4_SSL(self.account.imap_server, self.account.imap_port)
        else:
            mail = imaplib.IMAP4(self.account.imap_server, self.account.imap_port)
            
        try:
            mail.login(self.account.username, password)
            mail.select(self.account.fetch_folder)
            
            last_uid = self.persistence.get_cursor(self.account.account_id)
            logger.info(f"Fetching for {self.account.account_id} since UID: {last_uid}")
            
            status, response = mail.uid('SEARCH', None, f'UID {last_uid + 1}:*')
            
            if status != 'OK':
                logger.error(f"Failed to search for new emails: {status}")
                return []
                
            uids_str = response[0].decode('utf-8').strip()
            if not uids_str:
                return []
                
            uids = [int(u) for u in uids_str.split()]
            uids = [u for u in uids if u > last_uid]
            
            if not uids:
                return []

            if self.dry_run:
                logger.warning(f"[DRY-RUN] Will not fetch {len(uids)} emails. Identified UIDs: {uids}")
                return []

            fetched_emails = []
            max_uid_seen = last_uid

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

            # Directly update persistence for proof of concept logic
            if max_uid_seen > last_uid:
                self.persistence.update_cursor(self.account.account_id, max_uid_seen)
            
            return fetched_emails

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
        
    def _extract_body(self, msg: Message) -> str:
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    try:
                        part_body = part.get_payload(decode=True)
                        if part_body:
                            body += part_body.decode(errors='ignore')
                    except Exception:
                        pass
        else:
            try:
                part_body = msg.get_payload(decode=True)
                if part_body:
                    body = part_body.decode(errors='ignore')
            except Exception:
                body = str(msg.get_payload())
        return body
