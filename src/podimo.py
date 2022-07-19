from fastapi import HTTPException, Response

from gql import Client, gql
from gql.transport.exceptions import TransportQueryError
from gql.transport.requests import RequestsHTTPTransport

from feedgen.feed import FeedGenerator

import re
from mimetypes import guess_type
from time import time
from hashlib import sha256
from requests import head

GRAPHQL_URL = "https://graphql.pdm-gateway.com/graphql"


def podimo_podcast_to_rss(podimo_username: str, podimo_password: str, podcast_id: str,
                          feed_cache: dict, token_cache: dict, content_length_cache: dict):
    # Validate podcast ID
    id_pattern = re.compile('[0-9a-fA-F\-]+')
    if id_pattern.fullmatch(podcast_id) is None:
        raise HTTPException(status_code=400, detail="Invalid podcast id format.")

    # Authenticate
    auth_token, auth_hash = podimo_auth(podimo_username=podimo_username, podimo_password=podimo_password,
                                        token_cache=token_cache)

    # Get podcast data
    if (auth_hash, podcast_id) in feed_cache.keys() and feed_cache[(auth_hash, podcast_id)][1] > time():
        xml_feed = feed_cache[(auth_hash, podcast_id)][0]
    else:
        feed_data = podimo_get_podcast_data(podimo_token=auth_token, podcast_id=podcast_id)
        xml_feed = podcast_data_to_rss_feed(podimo_data=feed_data, content_length_cache=content_length_cache)
        # feed_cache[(auth_hash, podcast_id)] = (xml_feed, time() + 60 * 15)

    return Response(content=xml_feed, media_type='text/xml')


def podimo_auth(podimo_username: str, podimo_password: str, token_cache: dict):
    if '@' not in podimo_username:
        raise HTTPException(status_code=403, detail="Invalid e-mail address.")

    auth_hash = sha256(b'~'.join([podimo_username.encode('utf-8'), podimo_password.encode('utf-8')])).hexdigest()
    if auth_hash in token_cache.keys() and token_cache[auth_hash][1] > time():
        auth_token = token_cache[auth_hash][0]
    else:
        auth_token = podimo_get_authorization_token(podimo_username=podimo_username, podimo_password=podimo_password)
        token_cache[auth_hash] = (auth_token, time() + 3600 * 24)

    return auth_token, auth_hash


def podimo_get_authorization_token(podimo_username: str, podimo_password: str):
    t = RequestsHTTPTransport(
        url=GRAPHQL_URL,
        verify=True,
        retries=3,
    )
    client = Client(transport=t)
    query = gql(
        """
        query web_logInUser($email: String!, $password: String!) {  
            tokenWithCredentials(
                email: $email,
                password: $password
            ) {
                token    __typename  
            }
        }
        """
    )
    variables = {"email": podimo_username, "password": podimo_password}

    result = client.execute(query, variable_values=variables)
    return result['tokenWithCredentials']['token']


def podimo_get_podcast_data(podimo_token: str, podcast_id: str):
    data = {
        "podcast_info": None,
        "episodes": []
    }

    offset = 0
    chunk_size = 100
    while True:
        chunk_data = podimo_get_podcast_data_chunk(podimo_token=podimo_token, podcast_id=podcast_id, offset=offset)
        data['podcast_info'] = chunk_data['podcast']
        data['episodes'].extend(chunk_data['episodes'])

        if len(chunk_data['episodes']) < chunk_size:
            break
        else:
            offset += 100

    return data


def podcast_data_to_rss_feed(podimo_data, content_length_cache: dict):
    fg = FeedGenerator()
    fg.load_extension('podcast')

    # Podcast data
    podcast = podimo_data['podcast_info']
    fg.title(podcast['title'])
    fg.description(podcast['description'])
    fg.link(href=podcast['webAddress'], rel='alternate')
    fg.image(podcast['images']['coverImageUrl'])
    fg.language(podcast['language'])
    fg.author({'name': podcast['authorName']})

    # Episode data
    episodes = podimo_data['episodes']
    for episode in episodes:
        fe = fg.add_entry()
        fe.title(episode['title'])
        url = episode['streamMedia']['url']

        # Deal with Podimo's new URL structure.
        if "hls-media" in url and "/main.m3u8" in url:
            url = url.replace("hls-media", "audios")
            url = url.replace("/main.m3u8", ".mp3")

        mt, enc = guess_type(url)
        episode_length = get_content_length(url=url, content_length_cache=content_length_cache)
        fe.enclosure(url, episode_length, mt)
        fe.podcast.itunes_duration(episode['streamMedia']['duration'])
        fe.description(episode['description'])
        fe.pubDate(episode['datetime'])

    feed = fg.rss_str(pretty=True)
    return feed


def get_content_length(url: str, content_length_cache: dict):
    # I'm assuming the content length doesn't change, as that would mean the actual podcast episode has changed.
    if url in content_length_cache.keys():
        return content_length_cache[url]
    else:
        content_length = head(url).headers['content-length']
        content_length_cache[url] = content_length
        return content_length


def podimo_get_podcast_data_chunk(podimo_token: str, podcast_id: str, offset: int):
    t = RequestsHTTPTransport(
        url=GRAPHQL_URL,
        verify=True,
        retries=3,
        headers={
            'authorization': podimo_token
        }
    )
    client = Client(transport=t, serialize_variables=True)
    try:
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
            "offset": offset,
            "sorting": "PUBLISHED_DESCENDING"
        }

        result = client.execute(query, variable_values=variables)
        return result
    except TransportQueryError:
        raise HTTPException(status_code=500,
                            detail="Issue while fetching podcast {}. Likely this podcast does not exist.".format(
                                podcast_id))
