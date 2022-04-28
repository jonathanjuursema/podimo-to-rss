from os import getenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from podimo import podimo_podcast_to_rss

api = FastAPI()

# Add CORS middleware
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

PODIMO_USERNAME = str(getenv("PODIMO_USERNAME"))
PODIMO_PASSWORD = str(getenv("PODIMO_PASSWORD"))

FEED_CACHE = {}
TOKEN_CACHE = {}


@api.get("/podcast/{podcast_id}.xml")
async def root(podcast_id: str):
    podcast_feed = podimo_podcast_to_rss(podimo_username=PODIMO_USERNAME,
                                         podimo_password=PODIMO_PASSWORD,
                                         podcast_id=podcast_id,
                                         feed_cache=FEED_CACHE,
                                         token_cache=TOKEN_CACHE)

    return podcast_feed
