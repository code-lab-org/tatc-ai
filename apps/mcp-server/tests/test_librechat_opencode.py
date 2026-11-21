"""The OpenCode endpoints keep their plan, key, session header, and models in step."""

from tests.librechat_config import CONFIG_FILES, load_config

# One group at the Go base URL, which is the plan the workspace key uses.
ENDPOINTS = {
    "OpenCode": "https://opencode.ai/inference/go/openai/v1",
}
# model ID -> context window reported by the models endpoint.
MODELS = {
    "glm-5.3-flash": 1_000_000,
    "deepseek-v4.1-flash": 1_000_000,
    "kimi-k3": 1_048_576,
    "qwen3.8-flash": 1_000_000,
}


def _endpoints(path) -> dict[str, dict]:
    return {
        endpoint["name"]: endpoint
        for endpoint in load_config(path)["endpoints"]["custom"]
    }


def _opencode_specs(path) -> list[dict]:
    return [
        spec
        for spec in load_config(path)["modelSpecs"]["list"]
        if spec["preset"].get("endpoint") in ENDPOINTS
    ]


def test_endpoint_uses_a_user_key_and_the_go_base_url():
    for path in CONFIG_FILES:
        endpoints = _endpoints(path)
        for name, base_url in ENDPOINTS.items():
            endpoint = endpoints[name]
            assert endpoint["apiKey"] == "user_provided"
            assert endpoint["baseURL"] == base_url
            assert set(endpoint["models"]["default"]) == set(MODELS)


def test_titles_use_the_same_endpoint_and_models():
    for path in CONFIG_FILES:
        endpoints = _endpoints(path)
        for name, endpoint in endpoints.items():
            if name not in ENDPOINTS:
                continue
            assert endpoint["titleConvo"] is True
            assert endpoint["titleModel"] in endpoint["models"]["default"]


def test_session_header_keeps_a_user_prefix_for_title_requests():
    for path in CONFIG_FILES:
        for name in ENDPOINTS:
            session = _endpoints(path)[name]["headers"]["x-opencode-session"]
            # Go rejects an empty or missing header. Title requests carry no
            # conversation ID, so the user ID has to come first.
            assert session.startswith("{{LIBRECHAT_USER_ID}}")
            assert "{{LIBRECHAT_BODY_CONVERSATIONID}}" in session


def test_every_endpoint_model_has_a_tool_spec_and_context_window():
    expected = {(endpoint, model) for endpoint in ENDPOINTS for model in MODELS}
    for path in CONFIG_FILES:
        specs = _opencode_specs(path)
        assert len(specs) == len(expected)
        assert {
            (spec["preset"]["endpoint"], spec["preset"]["model"]) for spec in specs
        } == expected
        for spec in specs:
            assert spec["mcpServers"] == ["tatc"]
            # The picker group must name the plan that pays, or a Go model can
            # appear under the credit group and fail with "Insufficient balance".
            assert spec["group"] == spec["preset"]["endpoint"]
            model = spec["preset"]["model"]
            assert spec["preset"]["maxContextTokens"] == MODELS[model]


def test_development_and_deployment_files_agree():
    development, deployment = (_opencode_specs(path) for path in CONFIG_FILES)
    assert development == deployment
    for name in ENDPOINTS:
        assert _endpoints(CONFIG_FILES[0])[name] == _endpoints(CONFIG_FILES[1])[name]
