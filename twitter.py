"""Twitter source code and datastore model classes.
"""

__author__ = ['Ryan Barrett <bridgy@ryanb.org>']

import json
import logging
import re
import urllib

from activitystreams import twitter as as_twitter
from activitystreams.oauth_dropins import twitter as oauth_twitter
from activitystreams.source import SELF
import appengine_config
import models
import util

import webapp2


class Twitter(models.Source):
  """A Twitter account.

  The key name is the username.
  """

  DISPLAY_NAME = 'Twitter'
  SHORT_NAME = 'twitter'

  @staticmethod
  def new(handler, auth_entity=None):
    """Creates and returns a Twitter entity.

    Args:
      handler: the current RequestHandler
    """
    user = json.loads(auth_entity.user_json)
    return Twitter(key_name=user['screen_name'],
                   auth_entity=auth_entity,
                   url=Twitter.user_url(user['screen_name']),
                   name=user['name'],
                   picture=user['profile_image_url'])

  def __init__(self, *args, **kwargs):
    super(Twitter, self).__init__(*args, **kwargs)
    if self.auth_entity:
      self.as_source = as_twitter.Twitter(*self.auth_entity.access_token())

  def get_activities(self, fetch_replies=False, **kwargs):
    kwargs.setdefault('count', 100)
    activities = self.as_source.get_activities(
      group_id=SELF, user_id=self.key().name(), **kwargs)[1]

    if fetch_replies:
      self.fetch_replies(activities)
    return activities

  def fetch_replies(self, activities):
    """Fetches and injects replies into a list of activities, in place

    This searches for @-mentions, matches them to the original tweets with
    in_reply_to_status_id_str, and recurses until it's walked the entire tree.

    Args:
      activities: list of activity dicts

    Returns:
      same activities list
    """

    # cache searches for @-mentions for individual users. maps username to dict
    # mapping tweet id to ActivityStreams reply object dict.
    mentions = {}

    # find replies
    for activity in activities:
      # list of ActivityStreams reply object dict and set of seen activity ids
      # (tag URIs). seed with the original tweet; we'll filter it out later.
      replies = [activity]
      _, id = util.parse_tag_uri(activity['id'])
      seen_ids = set([id])

      for reply in replies:
        # get mentions of this tweet's author so we can search them for replies to
        # this tweet. can't use statuses/mentions_timeline because i'd need to
        # auth as the user being mentioned.
        # https://dev.twitter.com/docs/api/1.1/get/statuses/mentions_timeline
        #
        # note that these HTTP requests are synchronous. you can make async
        # requests by using urlfetch.fetch() directly, but not with urllib2.
        # https://developers.google.com/appengine/docs/python/urlfetch/asynchronousrequests
        author = reply['actor']['username']
        if author not in mentions:
          resp = self.as_source.urlread(as_twitter.API_SEARCH_URL %
                                        urllib.quote_plus('@' + author))
          mentions[author] = json.loads(resp)['statuses']

        # look for replies. add any we find to the end of replies. this makes us
        # recursively follow reply chains to their end. (python supports
        # appending to a sequence while you're iterating over it.)
        for mention in mentions[author]:
          id = mention['id_str']
          if (mention.get('in_reply_to_status_id_str') in seen_ids and
              id not in seen_ids):
            replies.append(self.as_source.tweet_to_activity(mention))
            seen_ids.add(id)

      activity['object']['replies'] = {
        'items': [r['object'] for r in replies[1:]],  # filter out seed activity
        'totalItems': len(replies),
        }

  @staticmethod
  def tweet_url(user, id):
    """Returns the URL of a tweet.
    """
    return 'http://twitter.com/%s/status/%d' % (user['screen_name'], id)

  @staticmethod
  def user_url(username):
    """Returns a user's URL.
    """
    return 'http://twitter.com/%s' % username


class AddTwitter(oauth_twitter.CallbackHandler):
  messages = set()

  def finish(self, auth_entity, state=None):
    tw = Twitter.create_new(self, auth_entity=auth_entity)
    util.added_source_redirect(self, tw)


application = webapp2.WSGIApplication([
    ('/twitter/start', oauth_twitter.StartHandler.to('/twitter/add')),
    ('/twitter/add', AddTwitter),
    ('/twitter/delete/finish', oauth_twitter.CallbackHandler.to('/delete/finish')),
    ], debug=appengine_config.DEBUG)
