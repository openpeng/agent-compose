"""Publish handlers for workflow steps."""


def to_blog(title: str, content: str) -> dict:
    """Publish content to a blog."""
    return {
        "title": title,
        "content": content,
        "status": "published",
        "url": f"https://blog.example.com/{title.lower().replace(' ', '-')}",
    }
