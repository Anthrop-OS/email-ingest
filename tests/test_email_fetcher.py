import os
import pytest
from unittest.mock import MagicMock, patch
from modules.email_fetcher import EmailFetcher
from core.config_loader import EmailAccountConfig
from core.persistence import PersistenceManager

@pytest.fixture
def mock_account():
    return EmailAccountConfig(
        account_id="test_account",
        imap_server="imap.example.com",
        imap_port=993,
        use_ssl=True,
        username="user@example.com",
        password_env_var="TEST_PWD",
        fetch_folder="INBOX"
    )

@pytest.fixture
def persistence():
    pm = PersistenceManager(":memory:")
    yield pm
    pm.close()

@patch.dict(os.environ, {"TEST_PWD": "dummy_password"})
def test_fetch_emails_dry_run(mock_account, persistence):
    fetcher = EmailFetcher(mock_account, persistence, is_dry_run=True)
    
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_instance = mock_imap.return_value
        mock_instance.login.return_value = ('OK', [b'Login successful'])
        mock_instance.select.return_value = ('OK', [b'10'])
        
        mock_instance.uid.side_effect = [
            ('OK', [b'101 102']), # SEARCH response
        ]
        
        emails, max_uid = fetcher.fetch_new_emails(start_uid=1)
        
        assert len(emails) == 0, "Dry run should not fetch full emails"
        mock_instance.select.assert_called_with('INBOX')
        assert max_uid == 102

@patch.dict(os.environ, {"TEST_PWD": "dummy_password"})
def test_fetch_emails_normal_run(mock_account, persistence):
    fetcher = EmailFetcher(mock_account, persistence, is_dry_run=False)
    
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_instance = mock_imap.return_value
        mock_instance.login.return_value = ('OK', [b'Login successful'])
        mock_instance.select.return_value = ('OK', [b'10'])
        
        mock_instance.uid.side_effect = [
            ('OK', [b'101 102']), # SEARCH response
            ('OK', [(b'101 (RFC822 {10}', b'Subject: A\r\n\r\nBody A')]), # FETCH 101, structure imaplib returns
            ('OK', [(b'102 (RFC822 {10}', b'Subject: B\r\n\r\nBody B')]), # FETCH 102
        ]
        
        emails, max_uid = fetcher.fetch_new_emails(start_uid=1)
        
        assert len(emails) == 2
        assert emails[0]['uid'] == 101
        assert emails[0]['subject'] == 'A'
        assert emails[1]['uid'] == 102
        
        assert max_uid == 102
