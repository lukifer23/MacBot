from macbot.auth import AuthStore
from macbot.config import Settings, prepare
from macbot.llm import LocalLLM


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Client:
    def __init__(self):
        self.paths = []

    def post(self, path, **kwargs):
        self.paths.append(path)
        if path.endswith("/apply-template"):
            return Response({"prompt": "rendered prompt"})
        return Response({"tokens": [1, 2, 3]})

    def close(self):
        return None


def test_llama_prompt_token_preparation_is_cached(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prepare(settings)
    auth = AuthStore(settings.data_dir)
    llm = LocalLLM(settings, auth)
    client = Client()
    llm.client.close()
    llm.client = client
    messages = [{"role": "user", "content": "hello"}]
    try:
        assert llm.count_tokens(messages) == 3
        assert llm.count_tokens(messages) == 3
        assert len(client.paths) == 2
        messages[0]["content"] = "different"
        assert llm.count_tokens(messages) == 3
        assert len(client.paths) == 4
    finally:
        llm.close()
        auth.close()
