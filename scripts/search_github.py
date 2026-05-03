import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def search_github(query, per_page=5):
    from urllib.parse import quote
    encoded_query = quote(query)
    url = f"https://api.github.com/search/repositories?q={encoded_query}&sort=updated&per_page={per_page}"
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Python')
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            data = json.loads(response.read())
            return data.get('items', [])
    except Exception as e:
        print(f"Error: {e}", file=__import__('sys').stderr)
        return []

def print_results(title, items):
    print(f"\n=== {title} ===")
    for item in items:
        pushed = item.get('pushed_at', '')[:10]
        if pushed >= '2026-04-19':
            desc = item.get('description', 'No description') or 'No description'
            print(f"{item['name']}|{item['html_url']}|{desc}|{pushed}")

# Search topics
searches = [
    ("Ollama optimization", "ollama optimization"),
    ("FastAPI LLM gateway", "fastapi llm gateway"),
    ("Multi-model routing", "llm router"),
    ("GPU concurrency", "gpu concurrency llm"),
    ("Context extension", "llm context extension"),
    ("vLLM serving", "vllm"),
    ("LiteLLM router", "litellm"),
]

for title, query in searches:
    items = search_github(query, 3)
    print_results(title, items)

print("\n\nDone!")
