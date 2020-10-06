import requests
import requests.auth
import logging
from http import cookiejar
from .rUtils import rNotif, rBase
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from ratelimit import sleep_and_retry, limits
from time import sleep

logging.basicConfig(level=logging.INFO, datefmt='%H:%M',
                    format='%(asctime)s, [%(filename)s:%(lineno)d] %(funcName)s(): %(message)s')
logger = logging.getLogger("logger")


class BlockAll(cookiejar.CookiePolicy):
    return_ok = set_ok = domain_return_ok = path_return_ok = lambda self, *args, **kwargs: False
    netscape = True
    rfc2965 = hide_cookie2 = False


class rBot:
    base = "https://oauth.reddit.com"

    def __init__(self, useragent, client_id, client_secret, bot_username, bot_pass):
        self.useragent = useragent
        self.client_id = client_id
        self.client_secret = client_secret
        self.bot_username = bot_username
        self.bot_pass = bot_pass
        self.req_sesh = self.prep_session()
        self.fetch_token()  # Fetch the token on instantioation (i cant spell for shit)

    @sleep_and_retry
    @limits(calls=30, period=60)
    def handled_req(self, method, url, **kwargs):
        while True:
            if method == 'POST':
                response = self.req_sesh.post(url, **kwargs)
            elif method == 'GET':
                response = self.req_sesh.get(url, **kwargs)
            elif method == 'PUT':
                response = self.req_sesh.put(url, **kwargs)
            else:
                raise NotImplementedError
            if response.status_code == 401:
                self.fetch_token()
                sleep(0.7)
                continue
            elif response.status_code == 403:
                logger.warning("Forbidden")
                return None
            else:
                return response

    def prep_session(self):
        req_sesh = requests.Session()
        retries = Retry(total=5,
                        backoff_factor=1,
                        status_forcelist=[500, 502, 503, 504, 404])
        req_sesh.mount('https://', HTTPAdapter(max_retries=retries))
        req_sesh.cookies.set_policy(BlockAll())
        req_sesh.headers.update({"User-Agent": self.useragent})
        return req_sesh

    def get_new_token(self, client_id_, client_code_, bot_username_, bot_pass_, useragent_):
        client_auth = requests.auth.HTTPBasicAuth(client_id_, client_code_)
        post_data = {"grant_type": "password", "username": bot_username_, "password": bot_pass_}
        response_token = requests.post(f"{rBase}/api/v1/access_token", auth=client_auth, data=post_data,
                                       headers={"User-Agent": useragent_})
        access_token = response_token.json()['access_token']
        return access_token

    def fetch_token(self):
        token = self.get_new_token(self.client_id, self.client_secret, self.bot_username, self.bot_pass, self.useragent)
        logger.info('got new token: ' + token)
        self.req_sesh.headers.update({"Authorization": f"bearer {token}"})

    def read_notifs(self, notifs):
        if type(notifs) != list:
            notifs = [notifs]

        ids = [notif.id_ for notif in notifs]
        ids = ','.join(ids)
        self.handled_req('POST', f"{self.base}/api/read_message", data={"id": ids})
        logger.info(f"read the notifs: {str([x for x in notifs])}")

    def del_comment(self, thingid):
        self.handled_req('POST', f"{self.base}/api/del", data={"id": thingid})
        logger.info(f"comment removed: {thingid}")

    def send_reply(self, text, thing):
        data = {'api_type': 'json', 'return_rtjson': '1', 'text': text, "thing_id": thing.id_}
        reply_req = self.handled_req('POST', f"{self.base}/api/comment", data=data)
        reply_s = reply_req.json()
        try:
            to_log = str(reply_s["json"]["errors"])
            logger.warning(to_log)
            sec_or_min = "min" if "minute" in to_log else "sec"
            num_in_err = int(''.join(list(filter(str.isdigit, to_log))))
            sleep_for = num_in_err + 2 if sec_or_min == "sec" else (num_in_err + 1) * 60 + 2
            logger.info(f"sleep for {sleep_for}")
            return sleep_for
        except KeyError:
            logger.info("message sent")
            return 0

    def check_last_comment_scores(self, limit=20):
        profile = self.handled_req('GET', f"{self.base}/user/{self.bot_username}/comments", params={"limit": limit})
        cm_bodies = profile.json()["data"]["children"]
        score_nd_id = {}
        for cm_body in cm_bodies:
            score_nd_id.update({cm_body["data"]["name"]: cm_body["data"]["score"]})
        return score_nd_id

    def check_inbox(self, rkind=None, read_if_not_rkind=True):
        unread_notifs_req = self.handled_req('GET', f"{self.base}/message/unread")
        unread_notifs = unread_notifs_req.json()['data']['children']

        for unread_notif in unread_notifs:
            the_notif = rNotif(unread_notif)
            if unread_notif['kind'] == rkind or rkind is None:
                yield the_notif
            elif read_if_not_rkind:
                self.read_notifs(the_notif)

    def get_info_by_id(self, thing_id):
        thing_info = self.handled_req('GET', f'{self.base}/api/info', params={"id": thing_id})
        try:
            thinginfo = thing_info.json()['data']['children'][0]
        except:
            thinginfo = None
        return thinginfo

    def exclude_from_all(self, sub):
        data = {'model': f'{{"name":"{sub}"}}'}
        self.handled_req('PUT', f'{self.base}/api/filter/user/{self.bot_username}/f/all/r/{sub}', data=data)

    def save_thing_by_id(self, thing_id):  # this for checking if the thing was seen before
        self.handled_req('POST', f'{self.base}/api/save', params={"id": thing_id})
        logger.info(f'{thing_id} saved')

    def create_or_update_multi(self, multiname, subs, visibility="private"):
        subreddits_d = []
        for sub in subs:
            subreddits_d.append(f'{{"name":"{sub}"}}')
        subs_quoted = ', '.join(subreddits_d)
        data = {
            'multipath': f'user/{self.bot_username}/m/{multiname}',
            'model': f'{{"subreddits":[{subs_quoted}], "visibility":"{visibility}"}}'
        }
        self.handled_req('PUT', f"{self.base}/api/multi/user/{self.bot_username}/m/{multiname}", data=data)
        logger.info(f'created or updated a multi named {multiname}')
