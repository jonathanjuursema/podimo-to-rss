import re

from os import getenv

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

from gql import Client, gql
from email.utils import parseaddr
from feedgen.feed import FeedGenerator
from gql.transport.requests import RequestsHTTPTransport
from random import choice, randint
from mimetypes import guess_type
from time import time
from hashlib import sha256
from requests import head

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
GRAPHQL_URL = "https://graphql.pdm-gateway.com/graphql"

tokens = dict()
feeds = dict()
token_timeout = 3600 * 24 * 5  # seconds = 5 days
feed_cache_time = 60 * 15  # seconds = 15 minutes


@api.get("/podcast/{podcast_id}.xml")
async def root(podcast_id: str):
    id_pattern = re.compile('[0-9a-fA-F\-]+')

    print(PODIMO_USERNAME)

    # Authenticate
    if not check_auth(PODIMO_USERNAME, PODIMO_PASSWORD):
        return authenticate()
    # Check if it is a valid podcast id string
    podcast_id = str(podcast_id)
    if id_pattern.fullmatch(podcast_id) is None:
        raise HTTPException(status_code=400, detail="Invalid podcast id format.")

    # Get a list of valid podcasts
    token, _ = tokens[token_key(PODIMO_USERNAME, PODIMO_PASSWORD)]
    try:
        podcasts = podcastsToRss(PODIMO_USERNAME, PODIMO_PASSWORD, podcast_id, getPodcasts(token, podcast_id))
    except Exception as e:
        exception = str(e)
        if "Podcast not found" in exception:
            raise HTTPException(status_code=404, detail="Podcast not found.")
        print(f"Error while fetching podcasts: {exception}")
        raise HTTPException(status_code=500, detail="Something went wrong fetching podcasts.")
    return Response(content=podcasts, media_type='text/xml')


def authenticate():
    return Response(f"401 Unauthorized.\nYou need to login with the correct credentials for Podimo.",
                    401,
                    {'Content-Type': 'text/plain'})


# Verify if it is actually an email address
def is_correct_email_address(username):
    return '@' in parseaddr(username)[1]


def token_key(username, password):
    key = sha256(b'~'.join([username.encode('utf-8'), password.encode('utf-8')])).hexdigest()
    return key


def check_auth(username, password):
    try:
        if len(username) > 256 or len(password) > 256:
            return False

        # Check if there is an authentication token already in memory. If so, use that one.
        # If it is expired, request a new token.
        key = token_key(username, password)
        if key in tokens:
            _, timestamp = tokens[key]
            if timestamp < time():
                del tokens[key]
            else:
                return True

        if is_correct_email_address(username):
            preauth_token = getPreregisterToken()
            prereg_id = getOnboardingId(preauth_token)
            token = podimoLogin(username, password, preauth_token, prereg_id)

            tokens[key] = (token, time() + token_timeout)
            return True
    except Exception as e:
        print(f"An error occurred: {e}")
    return False


def randomHexId(length):
    string = []
    hex_chars = list('1234567890abcdef')
    for i in range(length):
        string.append(choice(hex_chars))
    return "".join(string)


def randomFlyerId():
    a = randint(1000000000000, 9999999999999)
    b = randint(1000000000000, 9999999999999)
    return str(f"{a}-{b}")


def generateHeaders(authorization):
    headers = {
        # 'user-os': 'android',
        # 'user-agent': 'okhttp/4.9.1',
        # 'user-version': '2.15.3',
        # 'user-locale': 'nl-NL',
        'user-unique-id': randomHexId(16)
    }
    if authorization:
        headers['authorization'] = authorization
    return headers


def getPreregisterToken():
    t = RequestsHTTPTransport(
        url=GRAPHQL_URL,
        verify=True,
        retries=3,
        headers=generateHeaders(None)
    )
    client = Client(transport=t)
    query = gql(
        """
        query AuthorizationPreregisterUser($locale: String!, $referenceUser: String, $region: String, $appsFlyerId: String) {
            tokenWithPreregisterUser(
                locale: $locale
                referenceUser: $referenceUser
                region: $region
                source: WEB
                appsFlyerId: $appsFlyerId
            ) {
                token
            }
        }
        """
    )
    variables = {"locale": "nl-NL", "region": "nl", "appsFlyerId": randomFlyerId()}

    result = client.execute(query, variable_values=variables)
    return result['tokenWithPreregisterUser']['token']


def getOnboardingId(preauth_token):
    t = RequestsHTTPTransport(
        url=GRAPHQL_URL,
        verify=True,
        retries=3,
        headers=generateHeaders(preauth_token)
    )
    client = Client(transport=t)
    query = gql(
        """
        query OnboardingQuery {
            userOnboardingFlow {
                id
            }
        }
        """
    )
    result = client.execute(query)
    return result['userOnboardingFlow']['id']


def podimoLogin(username, password, preauth_token, prereg_id):
    t = RequestsHTTPTransport(
        url=GRAPHQL_URL,
        verify=True,
        retries=3,
        headers=generateHeaders(preauth_token)
    )
    client = Client(transport=t, serialize_variables=True)
    query = gql(
        """
        query AuthorizationAuthorize($email: String!, $password: String!, $locale: String!, $preregisterId: String) {
            tokenWithCredentials(
            email: $email
            password: $password
            locale: $locale
            preregisterId: $preregisterId
        ) {
            token
          }
        }
        """
    )
    variables = {"email": username, "password": password, "locale": "nl-NL", "preregisterId": prereg_id}

    result = client.execute(query, variable_values=variables)
    return result['tokenWithCredentials']['token']


def getPodcasts(token, podcast_id):
    t = RequestsHTTPTransport(
        url=GRAPHQL_URL,
        verify=True,
        retries=3,
        headers=generateHeaders(token)
    )
    client = Client(transport=t, serialize_variables=True)
    query = gql(
        """
        query ChannelEpisodesQuery($podcastId: String!, $limit: Int!, $offset: Int!, $sorting: PodcastEpisodeSorting) {
          episodes: podcastEpisodes(
            podcastId: $podcastId
            converted: true
            published: true
            limit: $limit
            offset: $offset
            sorting: $sorting
          ) {
            ...EpisodeBase
          }
          podcast: podcastById(podcastId: $podcastId) {
            title
            description
            webAddress
            authorName
            language
            images {
                coverImageUrl
            }
          }
        }

        fragment EpisodeBase on PodcastEpisode {
          description
          datetime
          title
          streamMedia {
            duration
            url
          }
        }
        """
    )
    variables = {
        "podcastId": podcast_id,
        "limit": 100,
        "offset": 0,
        "sorting": "PUBLISHED_DESCENDING"
    }

    result = client.execute(query, variable_values=variables)
    return result


def contentLengthOfUrl(username, password, url):
    token, _ = tokens[token_key(username, password)]
    return head(url, headers=generateHeaders(token)).headers['content-length']


def podcastsToRss(username, password, podcast_id, data):
    key = (token_key(username, password), podcast_id)
    if key in feeds:
        feed, timestamp = feeds[key]
        if timestamp < time():
            del feeds[key]
        else:
            return feed
    else:
        fg = FeedGenerator()
        fg.load_extension('podcast')

        podcast = data['podcast']
        fg.title(podcast['title'])
        fg.description(podcast['description'])
        fg.link(href=podcast['webAddress'], rel='alternate')
        fg.image(podcast['images']['coverImageUrl'])
        fg.language(podcast['language'])
        fg.author({'name': podcast['authorName']})
        episodes = data['episodes']
        for episode in episodes:
            fe = fg.add_entry()
            fe.title(episode['title'])
            url = episode['streamMedia']['url']
            mt, enc = guess_type(url)
            fe.enclosure(url, contentLengthOfUrl(username, password, url), mt)
            fe.podcast.itunes_duration(episode['streamMedia']['duration'])
            fe.description(episode['description'])
            fe.pubDate(episode['datetime'])

        feed = fg.rss_str(pretty=True)
        expiry = time() + feed_cache_time
        feeds[key] = (feed, expiry)
        return feed
