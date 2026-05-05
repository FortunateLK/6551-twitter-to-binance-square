#!/usr/bin/env python3
"""
Twitter/X to Binance Square auto-mirror, plus Binance Square token heat lookup.

The original skill mirrors Twitter/X content into Binance Square.  This version
keeps that workflow and adds a read-only `square_heat` mode that searches
Binance Square content for a token and scores its discussion heat.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TWITTER_SEARCH_URL = "https://ai.6551.io/open/twitter_search"
TWITTER_USER_TWEETS_URL = "https://ai.6551.io/open/twitter_user_tweets"
SQUARE_POST_URL = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"

DEFAULT_SQUARE_SEARCH_URLS = [
    # Binance changes public web endpoints over time. Keep these configurable
    # and normalize whatever shape the API returns.
    "https://www.binance.com/bapi/composite/v1/public/pgc/feed/search",
    "https://www.binance.com/bapi/composite/v1/public/cms/article/search/query",
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def log(message: str) -> None:
    print(f"[{utc_now().isoformat(timespec='seconds')}] {message}", flush=True)


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
    retries: int = 2,
) -> dict[str, Any]:
    body = None
    req_headers = {"Content-Type": "application/json", "User-Agent": "binanceSkill/0.2"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, data=body, headers=req_headers, method=method)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8")
            if not text:
                return {}
            return json.loads(text)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"Request failed: {method} {url}: {exc}") from exc
    raise RuntimeError(f"Request failed: {method} {url}: {last_error}")


def load_json_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json_file(path: str, data: dict[str, Any]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def deep_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield item
            yield from deep_values(item)
    elif isinstance(value, list):
        for item in value:
            yield item
            yield from deep_values(item)


def find_lists(value: Any) -> Iterable[list[Any]]:
    if isinstance(value, list):
        yield value
        for item in value:
            yield from find_lists(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from find_lists(item)


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return parse_datetime(int(text))
        text = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def first_present(data: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def int_or_zero(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.-]", "", value)
        if cleaned in ("", "-", "."):
            return 0
        try:
            return int(float(cleaned))
        except ValueError:
            return 0
    return 0


@dataclass
class SquarePost:
    post_id: str
    author_id: str
    author_name: str
    text: str
    created_at: datetime | None
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def engagement(self) -> int:
        return self.likes + self.comments * 2 + self.shares * 3

    @property
    def age_hours(self) -> float | None:
        if self.created_at is None:
            return None
        return max((utc_now() - self.created_at).total_seconds() / 3600, 0)


@dataclass
class HeatReport:
    token: str
    query: str
    window_hours: int
    fetched_posts: int
    posts_in_window: int
    unique_authors: int
    total_likes: int
    total_comments: int
    total_shares: int
    total_views: int
    weighted_engagement: int
    recent_posts_1h: int
    recent_posts_6h: int
    momentum_ratio: float
    heat_score: int
    top_posts: list[dict[str, Any]]


def normalize_square_post(item: dict[str, Any]) -> SquarePost | None:
    author = first_present(item, ["author", "user", "profile", "publisher"], {}) or {}
    if not isinstance(author, dict):
        author = {}

    post_id = str(first_present(item, ["id", "postId", "articleId", "contentId", "feedId"], ""))
    text = first_present(
        item,
        ["bodyTextOnly", "body", "content", "text", "title", "summary", "description"],
        "",
    )
    if isinstance(text, dict):
        text = " ".join(str(v) for v in text.values() if isinstance(v, (str, int, float)))
    text = str(text or "").strip()

    if not post_id:
        post_id = str(abs(hash(json.dumps(item, sort_keys=True, default=str))))
    if not text:
        nested_strings = [str(v).strip() for v in deep_values(item) if isinstance(v, str) and len(v.strip()) > 20]
        text = " ".join(nested_strings[:2])
    if not text:
        return None

    created_at = parse_datetime(
        first_present(
            item,
            ["createTime", "createdAt", "publishTime", "postTime", "insertTime", "updateTime", "time"],
        )
    )

    return SquarePost(
        post_id=post_id,
        author_id=str(first_present(author, ["id", "userId", "authorId"], "")),
        author_name=str(first_present(author, ["name", "nickname", "userName", "displayName"], "")),
        text=text,
        created_at=created_at,
        likes=int_or_zero(first_present(item, ["likeCount", "likes", "thumbUpCount", "favoriteCount"])),
        comments=int_or_zero(first_present(item, ["commentCount", "comments", "replyCount"])),
        shares=int_or_zero(first_present(item, ["shareCount", "shares", "forwardCount"])),
        views=int_or_zero(first_present(item, ["viewCount", "views", "readCount"])),
        raw=item,
    )


def extract_square_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for maybe_list in find_lists(response):
        dict_items = [item for item in maybe_list if isinstance(item, dict)]
        if len(dict_items) > len(candidates):
            candidates = dict_items
    return candidates


def token_queries(token: str) -> list[str]:
    clean = token.strip()
    symbol = clean.upper().lstrip("$#")
    values = [clean, symbol, f"${symbol}", f"#{symbol}"]
    seen: set[str] = set()
    return [value for value in values if value and not (value in seen or seen.add(value))]


def square_search_payload(query: str, limit: int, page: int = 1) -> dict[str, Any]:
    return {
        "keyword": query,
        "query": query,
        "searchKey": query,
        "page": page,
        "pageIndex": page,
        "pageNo": page,
        "pageSize": limit,
        "rows": limit,
        "size": limit,
    }


def fetch_square_posts(config: dict[str, Any]) -> list[SquarePost]:
    token = str(config.get("token") or config.get("keywords") or "").strip()
    if not token:
        raise ValueError("square_heat mode requires --token or config.token")

    limit = int(config.get("square_max_results", config.get("max_results", 50)))
    endpoints = config.get("square_search_urls") or os.getenv("SQUARE_SEARCH_URL") or DEFAULT_SQUARE_SEARCH_URLS
    if isinstance(endpoints, str):
        endpoints = [value.strip() for value in endpoints.split(",") if value.strip()]

    headers = {
        "clienttype": str(config.get("square_client_type", "binanceSkill")),
        "lang": str(config.get("square_lang", "en")),
    }
    if os.getenv("SQUARE_API_KEY"):
        headers["X-Square-OpenAPI-Key"] = os.getenv("SQUARE_API_KEY", "")

    all_posts: dict[str, SquarePost] = {}
    errors: list[str] = []
    for endpoint in endpoints:
        for query in token_queries(token):
            payload = square_search_payload(query, limit)
            try:
                response = request_json("POST", endpoint, headers=headers, payload=payload, retries=1)
            except RuntimeError as exc:
                errors.append(str(exc))
                continue

            items = extract_square_items(response)
            for item in items:
                post = normalize_square_post(item)
                if post:
                    all_posts[post.post_id] = post
            if all_posts:
                log(f"Fetched {len(all_posts)} Binance Square posts via {endpoint}")
                return list(all_posts.values())

    if errors:
        log("Square search endpoints returned no usable data. Last error: " + errors[-1])
    return []


def filter_token_posts(posts: list[SquarePost], token: str) -> list[SquarePost]:
    symbol = re.escape(token.upper().lstrip("$#"))
    pattern = re.compile(rf"(?<![A-Z0-9])[$#]?{symbol}(?![A-Z0-9])", re.IGNORECASE)
    return [post for post in posts if pattern.search(post.text)]


def score_heat(posts: list[SquarePost], token: str, window_hours: int, top_n: int = 5) -> HeatReport:
    now = utc_now()
    windowed: list[SquarePost] = []
    for post in posts:
        if post.created_at is None:
            windowed.append(post)
            continue
        age_hours = (now - post.created_at).total_seconds() / 3600
        if age_hours <= window_hours:
            windowed.append(post)

    authors = {post.author_id or post.author_name or post.post_id for post in windowed}
    total_likes = sum(post.likes for post in windowed)
    total_comments = sum(post.comments for post in windowed)
    total_shares = sum(post.shares for post in windowed)
    total_views = sum(post.views for post in windowed)
    weighted_engagement = sum(post.engagement for post in windowed)
    recent_posts_1h = sum(1 for post in windowed if post.age_hours is not None and post.age_hours <= 1)
    recent_posts_6h = sum(1 for post in windowed if post.age_hours is not None and post.age_hours <= 6)

    expected_6h = max(len(windowed) * min(6, window_hours) / max(window_hours, 1), 1)
    momentum_ratio = recent_posts_6h / expected_6h

    post_score = math.log1p(len(windowed)) * 22
    author_score = math.log1p(len(authors)) * 18
    engagement_score = math.log1p(weighted_engagement) * 12
    view_score = math.log1p(total_views) * 4
    momentum_score = min(momentum_ratio, 3) * 12
    heat_score = int(max(0, min(100, post_score + author_score + engagement_score + view_score + momentum_score)))

    top_posts = sorted(windowed, key=lambda post: (post.engagement, post.views), reverse=True)[:top_n]
    return HeatReport(
        token=token.upper().lstrip("$#"),
        query=" / ".join(token_queries(token)),
        window_hours=window_hours,
        fetched_posts=len(posts),
        posts_in_window=len(windowed),
        unique_authors=len(authors),
        total_likes=total_likes,
        total_comments=total_comments,
        total_shares=total_shares,
        total_views=total_views,
        weighted_engagement=weighted_engagement,
        recent_posts_1h=recent_posts_1h,
        recent_posts_6h=recent_posts_6h,
        momentum_ratio=round(momentum_ratio, 2),
        heat_score=heat_score,
        top_posts=[
            {
                "post_id": post.post_id,
                "author": post.author_name or post.author_id,
                "created_at": post.created_at.isoformat() if post.created_at else None,
                "likes": post.likes,
                "comments": post.comments,
                "shares": post.shares,
                "views": post.views,
                "engagement": post.engagement,
                "text": post.text[:280],
            }
            for post in top_posts
        ],
    )


def print_heat_report(report: HeatReport, output_json: bool = False) -> None:
    data = asdict(report)
    if output_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print(f"\nBinance Square token heat: ${report.token}")
    print(f"Window: {report.window_hours}h | Query: {report.query}")
    print(f"Heat score: {report.heat_score}/100")
    print(
        "Posts: {posts} | Authors: {authors} | 1h: {h1} | 6h: {h6} | Momentum: {momentum}x".format(
            posts=report.posts_in_window,
            authors=report.unique_authors,
            h1=report.recent_posts_1h,
            h6=report.recent_posts_6h,
            momentum=report.momentum_ratio,
        )
    )
    print(
        "Engagement: likes={likes}, comments={comments}, shares={shares}, views={views}, weighted={weighted}".format(
            likes=report.total_likes,
            comments=report.total_comments,
            shares=report.total_shares,
            views=report.total_views,
            weighted=report.weighted_engagement,
        )
    )
    if report.top_posts:
        print("\nTop posts:")
        for idx, post in enumerate(report.top_posts, start=1):
            print(f"{idx}. {post['author'] or 'unknown'} | engagement={post['engagement']} | {post['text']}")


def run_square_heat(config: dict[str, Any], output_json: bool = False) -> HeatReport:
    token = str(config.get("token") or config.get("keywords") or "").strip()
    window_hours = int(config.get("heat_window_hours", 24))
    top_n = int(config.get("heat_top_posts", 5))
    posts = fetch_square_posts(config)
    token_posts = filter_token_posts(posts, token)
    report = score_heat(token_posts, token, window_hours, top_n)
    print_heat_report(report, output_json=output_json)
    return report


def fetch_tweets(config: dict[str, Any]) -> list[dict[str, Any]]:
    token = os.getenv("TWITTER_TOKEN")
    if not token:
        raise RuntimeError("TWITTER_TOKEN is required for Twitter mirror modes")

    mode = config.get("mode", "account")
    headers = {"Authorization": f"Bearer {token}"}
    max_results = int(config.get("max_results", config.get("max_posts_per_run", 10)))

    if mode == "account":
        tweets: list[dict[str, Any]] = []
        for username in config.get("accounts", []):
            payload = {
                "username": username,
                "maxResults": max_results,
                "product": config.get("product", "Latest"),
                "includeReplies": bool(config.get("include_replies", False)),
                "includeRetweets": bool(config.get("include_retweets", False)),
            }
            response = request_json("POST", TWITTER_USER_TWEETS_URL, headers=headers, payload=payload)
            tweets.extend(extract_square_items(response))
        return tweets

    payload = {
        "maxResults": max_results,
        "product": config.get("product", "Top"),
        "minLikes": int(config.get("min_likes", 0)),
    }
    if mode == "hashtag":
        payload["hashtag"] = str(config.get("hashtag", "")).lstrip("#")
    else:
        payload["keywords"] = config.get("keywords", "")
    response = request_json("POST", TWITTER_SEARCH_URL, headers=headers, payload=payload)
    return extract_square_items(response)


def transform_tweet(tweet: dict[str, Any], config: dict[str, Any]) -> str:
    text = str(first_present(tweet, ["text", "content", "full_text"], ""))
    text = re.sub(r"https://t\.co/\S+", "", text).strip()
    username = first_present(tweet, ["username", "userName", "screen_name"], "")
    lines = [text]
    if config.get("show_source", True) and username:
        lines.append(f"Source: @{username} on X")
    if config.get("show_tool_attribution", True):
        lines.append("Publish by using 6551 twitter mirror tool")
    hashtags = config.get("add_hashtags", [])
    if hashtags:
        lines.append(" ".join(tag if str(tag).startswith("#") else f"#{tag}" for tag in hashtags))
    return "\n\n".join(line for line in lines if line)[:4000]


def post_to_square(content: str) -> dict[str, Any]:
    api_key = os.getenv("SQUARE_API_KEY")
    if not api_key:
        raise RuntimeError("SQUARE_API_KEY is required for posting to Binance Square")
    return request_json(
        "POST",
        SQUARE_POST_URL,
        headers={"X-Square-OpenAPI-Key": api_key, "clienttype": "binanceSkill"},
        payload={"bodyTextOnly": content},
    )


def run_mirror_once(config: dict[str, Any]) -> None:
    state_file = str(config.get("state_file", "mirror_state.json"))
    state = load_json_file(state_file)
    posted = set(state.get("posted_tweet_ids", []))
    tweets = fetch_tweets(config)
    max_posts = int(config.get("max_posts_per_run", 5))
    posted_count = 0

    for tweet in tweets:
        tweet_id = str(first_present(tweet, ["id", "tweet_id", "tweetId"], ""))
        if tweet_id and tweet_id in posted:
            continue
        content = transform_tweet(tweet, config)
        if not content:
            continue
        if config.get("dry_run", False):
            print("\n--- DRY RUN Square post ---")
            print(content)
        else:
            response = post_to_square(content)
            log(f"Posted tweet {tweet_id or 'unknown'} to Square: {response}")
        if tweet_id:
            posted.add(tweet_id)
        posted_count += 1
        if posted_count >= max_posts:
            break

    state["posted_tweet_ids"] = sorted(posted)
    state["last_poll_time"] = utc_now().isoformat()
    save_json_file(state_file, state)


def merge_config(args: argparse.Namespace) -> dict[str, Any]:
    config = load_json_file(args.config)
    for key, value in vars(args).items():
        if key == "config" or value is None:
            continue
        config[key] = value
    if isinstance(config.get("accounts"), str):
        config["accounts"] = [item.strip() for item in config["accounts"].split(",") if item.strip()]
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Twitter mirror and Binance Square token heat tool")
    parser.add_argument("--config", help="Path to JSON config")
    parser.add_argument("--mode", choices=["account", "search", "hashtag", "square_heat"], default=None)
    parser.add_argument("--accounts", help="Comma-separated Twitter accounts")
    parser.add_argument("--keywords", help="Twitter keywords or token query")
    parser.add_argument("--hashtag", help="Twitter hashtag")
    parser.add_argument("--token", help="Token symbol for square_heat, e.g. BTC or $BTC")
    parser.add_argument("--interval", dest="poll_interval_seconds", type=int)
    parser.add_argument("--max-results", dest="max_results", type=int)
    parser.add_argument("--square-max-results", dest="square_max_results", type=int)
    parser.add_argument("--heat-window-hours", type=int)
    parser.add_argument("--heat-top-posts", type=int)
    parser.add_argument("--state-file")
    parser.add_argument("--dry-run", action="store_true", default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON for square_heat")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = merge_config(args)
    mode = config.get("mode", "account")

    if mode == "square_heat":
        run_square_heat(config, output_json=bool(args.json))
        return 0

    run_mirror_once(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
