

def test_secret_firewall_allows_secret_loader_function_assignment():
    from local_ai_control.services.security import SecretFirewall

    text = 'api_key = read_keychain_secret("gemini")'

    decision = SecretFirewall().inspect(text)

    assert decision.action == "ALLOW"


def test_secret_firewall_still_blocks_literal_api_key_assignment():
    from local_ai_control.services.security import SecretFirewall

    value = "abcdefghijklmnop" + "1234567890"
    text = f'api_key = "{value}"'

    decision = SecretFirewall().inspect(text)

    assert decision.action == "BLOCK"
    assert decision.category == "generic_secret_assignment"
