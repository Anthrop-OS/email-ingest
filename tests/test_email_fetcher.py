import os
import pytest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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


# ── HTML parsing tests ───────────────────────────────────────────

@pytest.fixture
def fetcher(mock_account, persistence):
    return EmailFetcher(mock_account, persistence)


def test_extract_body_html_only(fetcher):
    """Multipart email with only text/html should return cleaned text."""
    msg = MIMEMultipart()
    html_part = MIMEText("<html><body><p>Hello World</p></body></html>", "html")
    msg.attach(html_part)
    body = fetcher._extract_body(msg)
    assert "Hello World" in body


def test_extract_body_prefers_plain(fetcher):
    """When both plain and html are present, prefer plain."""
    msg = MIMEMultipart("alternative")
    plain_part = MIMEText("Plain text content", "plain")
    html_part = MIMEText("<html><body><p>HTML content</p></body></html>", "html")
    msg.attach(plain_part)
    msg.attach(html_part)
    body = fetcher._extract_body(msg)
    assert body.strip() == "Plain text content"


def test_html_to_text_strips_scripts(fetcher):
    """Script and style tags should be completely removed."""
    html = "<html><head><title>T</title></head><body><script>alert('x')</script><style>.a{}</style><p>Content</p></body></html>"
    result = fetcher._html_to_text(html)
    assert "alert" not in result
    assert ".a{}" not in result
    assert "Content" in result


def test_html_to_text_preserves_links(fetcher):
    """Link text and href should be preserved."""
    html = '<html><body><a href="https://example.com">Click here</a></body></html>'
    result = fetcher._html_to_text(html)
    assert "Click here" in result
    assert "https://example.com" in result
