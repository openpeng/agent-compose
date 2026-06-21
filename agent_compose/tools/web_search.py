"""
web_search 工具 - 网络搜索

支持:
- DuckDuckGo (默认, 无需 API key)
- Google (需要 API key + CSE ID)
- Bing (需要 API key)
"""

import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional


async def _search_duckduckgo(query: str, max_results: int) -> List[Dict[str, str]]:
    """DuckDuckGo HTML 搜索"""
    import httpx

    encoded_query = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()

        html = response.text
        results = []

        # 解析搜索结果
        # DuckDuckGo HTML 结果格式
        result_blocks = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        for i, (href, title_html) in enumerate(result_blocks[:max_results]):
            # 清理标题中的 HTML 标签
            title = re.sub(r"<[^>]+>", "", title_html).strip()
            # 提取摘要（尝试找到对应的摘要）
            snippet = ""
            snippet_match = re.search(
                rf'<a[^>]*href="{re.escape(href)}"[^>]*>.*?</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            )
            if snippet_match:
                snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()

            # 处理 DuckDuckGo 重定向 URL
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://duckduckgo.com" + href

            results.append({
                "title": title,
                "url": href,
                "snippet": snippet,
            })

        return results


async def _search_google(
    query: str, max_results: int, api_key: str, search_engine_id: str
) -> List[Dict[str, str]]:
    """Google Custom Search API"""
    import httpx

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": search_engine_id,
        "q": query,
        "num": min(max_results, 10),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params)

        if response.status_code != 200:
            data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            error = data.get("error", {}).get("message", response.text[:200])
            raise RuntimeError(f"web_search: Google API error ({response.status_code}): {error}")

        data = response.json()
        items = data.get("items", [])

        return [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in items[:max_results]
        ]


async def _search_bing(query: str, max_results: int, api_key: str) -> List[Dict[str, str]]:
    """Bing Search API"""
    import httpx

    url = "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": api_key}
    params = {"q": query, "count": min(max_results, 50)}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers, params=params)

        if response.status_code != 200:
            error = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            msg = error.get("error", {}).get("message", response.text[:200]) if isinstance(error, dict) else response.text[:200]
            raise RuntimeError(f"web_search: Bing API error ({response.status_code}): {msg}")

        data = response.json()
        web_pages = data.get("webPages", {}).get("value", [])

        return [
            {
                "title": page.get("name", ""),
                "url": page.get("url", ""),
                "snippet": page.get("snippet", ""),
            }
            for page in web_pages[:max_results]
        ]


async def web_search_tool(args: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    网络搜索

    Args:
        args: {
            "query": str,                   # 必需: 搜索查询
            "engine": str,                  # 可选: "duckduckgo" | "google" | "bing", 默认 "duckduckgo"
            "max_results": int,             # 可选: 最大结果数, 默认 10
            "api_key": str,                 # 可选: API 密钥 (Google/Bing 需要)
            "search_engine_id": str,        # 可选: Google CSE ID
            "language": str,                # 可选: 语言
            "region": str,                  # 可选: 地区
        }

    Returns:
        {
            "results": List[{title, url, snippet}],
            "query": str,
            "engine": str,
        }
    """
    query = args.get("query")
    if not query or not isinstance(query, str):
        raise ValueError("web_search: 'query' parameter is required and must be a string")

    engine = args.get("engine", "duckduckgo")
    max_results = args.get("max_results", 10)
    if max_results <= 0:
        max_results = 10
    if max_results > 50:
        max_results = 50

    if engine == "google":
        api_key = args.get("api_key") or os.environ.get("GOOGLE_API_KEY")
        cse_id = args.get("search_engine_id") or os.environ.get("GOOGLE_CSE_ID")
        if not api_key or not cse_id:
            raise ValueError("web_search: Google search requires api_key and search_engine_id. Set GOOGLE_API_KEY and GOOGLE_CSE_ID environment variables or pass them as arguments.")
        results = await _search_google(query, max_results, api_key, cse_id)

    elif engine == "bing":
        api_key = args.get("api_key") or os.environ.get("BING_API_KEY")
        if not api_key:
            raise ValueError("web_search: Bing search requires api_key. Set BING_API_KEY environment variable or pass api_key.")
        results = await _search_bing(query, max_results, api_key)

    else:
        # DuckDuckGo (默认)
        results = await _search_duckduckgo(query, max_results)

    return {
        "results": results,
        "query": query,
        "engine": engine,
    }
