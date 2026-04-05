import pytest
from pathlib import Path
from modules.output_channel import ConsoleOutputChannel

def test_console_channel_rendering(tmp_path, capsys):
    # Setup temporary template
    d_templates = tmp_path / "templates"
    d_templates.mkdir()
    p_template = d_templates / "dummy_template.j2"
    p_template.write_text("Hello UID: {{ original_uid }}, Priority: {{ priority }}")
    
    channel = ConsoleOutputChannel(
        template_dir=str(d_templates),
        template_name="dummy_template.j2"
    )
    
    mock_data = [
        {"original_uid": 105, "priority": "High"}
    ]
    
    result = channel.emit("worker@test.com", mock_data)
    assert result is True
    
    captured = capsys.readouterr()
    assert "Hello UID: 105, Priority: High" in captured.out

def test_console_channel_fallback_json(capsys):
    # Pass a non-existent template dir to trigger fallback
    channel = ConsoleOutputChannel(str(Path("non_existent_fake_dir")))
    
    mock_data = [
        {"original_uid": 999, "priority": "Spam"}
    ]
    
    result = channel.emit("foo@bar.com", mock_data)
    assert result is True
    
    captured = capsys.readouterr()
    assert '"original_uid": 999' in captured.out
    assert '"priority": "Spam"' in captured.out
