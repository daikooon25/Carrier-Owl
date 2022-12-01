from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException
import os
import time
import yaml
import datetime
import slackweb
import argparse
import textwrap
from bs4 import BeautifulSoup
import warnings
import urllib.parse
from dataclasses import dataclass
import arxiv
import requests

from notion_client import Client
# setting
warnings.filterwarnings('ignore')


@dataclass
class Result:
    url: str
    title: str
    abstract: str
    keywords: list
    score: float = 0.0


def calc_score(abst: str, keywords: dict):
    sum_score = 0.0
    hit_kwd_list = []

    for word in keywords.keys():
        score = keywords[word]
        if word.lower() in abst.lower():
            sum_score += score
            hit_kwd_list.append(word)
    return sum_score, hit_kwd_list


def search_keyword(
        articles: list, keywords: dict, score_threshold: float
        ) -> list:
    results = []

    # ヘッドレスモードでブラウザを起動
    options = Options()
    options.add_argument('--headless')

    # ブラウザーを起動
    driver = webdriver.Firefox(executable_path=ChromeDriverManager().install(), options=options)

    for article in articles:
        url = article['arxiv_url']
        title = article['title']
        abstract = article['summary']
        score, hit_keywords = calc_score(abstract, keywords)
        if (score != 0) and (score >= score_threshold):
            title_trans = get_translated_text('ja', 'en', title, driver)
            abstract = abstract.replace('\n', '')
            abstract_trans = get_translated_text('ja', 'en', abstract, driver)
            # abstract_trans = textwrap.wrap(abstract_trans, 40)  # 40行で改行
            # abstract_trans = '\n'.join(abstract_trans)
            result = Result(
                    url=url, title=title_trans, abstract=abstract_trans,
                    score=score, keywords=hit_keywords)
            results.append(result)

    # ブラウザ停止
    driver.quit()
    return results


def send2app(text: str, slack_id: str, line_token: str) -> None:
    # slack
    if slack_id is not None:
        slack = slackweb.Slack(url=slack_id)
        slack.notify(text=text)

    # line
    if line_token is not None:
        line_notify_api = 'https://notify-api.line.me/api/notify'
        headers = {'Authorization': f'Bearer {line_token}'}
        data = {'message': f'message: {text}'}
        requests.post(line_notify_api, headers=headers, data=data)


def post2notion(results: list, dt_now: datetime.datetime, notion_token: str, notion_database_id: str) -> None:
    notion = Client(auth=notion_token)
    created_at = dt_now.strftime('%Y-%m-%d')
    for result in sorted(results, reverse=True, key=lambda x: x.score):
        title = result.title
        keywords = result.keywords
        abstract = result.abstract
        url = result.url
        score = result.score

        notion.pages.create(**{
            'parent': {
                'database_id': notion_database_id
            },
            'properties': {
                'Title': {
                    'title': [{'text': {'content': title}}]
                },
                'Keywords': {
                    'multi_select': [{'name': word} for word in keywords]
                },
                'Abstract': {
                    'rich_text': [{'type': 'text', 'text': {'content': abstract}}]
                },
                'URL': {
                    'url': url
                },
                'Score': {
                    'number': score
                },
                'Created at': {
                    'date': {'start': created_at}
                }
            }
        })


def get_translated_text(from_lang: str, to_lang: str, from_text: str, driver) -> str:
    '''
    https://qiita.com/fujino-fpu/items/e94d4ff9e7a5784b2987
    '''
    sleep_time = 1

    # urlencode
    from_text = urllib.parse.quote(from_text)

    # url作成
    url = 'https://www.deepl.com/translator#' \
        + from_lang + '/' + to_lang + '/' + from_text

    driver.get(url)
    driver.implicitly_wait(10)  # 見つからないときは、10秒まで待つ

    for i in range(30):
        # 指定時間待つ
        time.sleep(sleep_time)
        html = driver.page_source
        # to_text = get_text_from_page_source(html)
        to_text = get_text_from_driver(driver)

        if to_text:
            break
    if to_text is None:
        return urllib.parse.unquote(from_text)
    return to_text


def get_text_from_driver(driver) -> str:
    try:
        elem = driver.find_element_by_class_name('lmt__translations_as_text__text_btn')
    except NoSuchElementException as e:
        return None
    text = elem.get_attribute('innerHTML')
    return text


def get_text_from_page_source(html: str) -> str:
    soup = BeautifulSoup(html, features='lxml')
    target_elem = soup.find(class_="lmt__translations_as_text__text_btn")
    text = target_elem.text
    return text


def get_config() -> dict:
    file_abs_path = os.path.abspath(__file__)
    file_dir = os.path.dirname(file_abs_path)
    config_path = f'{file_dir}/../config.yaml'
    with open(config_path, 'r') as yml:
        config = yaml.load(yml)
    return config


def main():
    # debug用
    parser = argparse.ArgumentParser()
    parser.add_argument('--slack_id', default=None)
    parser.add_argument('--line_token', default=None)
    parser.add_argument('--notion_token', default=None)
    parser.add_argument('--notion_database_id', default=None)
    args = parser.parse_args()

    config = get_config()
    subject = config['subject']
    keywords = config['keywords']
    score_threshold = float(config['score_threshold'])

    dt_now = datetime.datetime.now()
    day_before_yesterday = dt_now - datetime.timedelta(days=2)
    day_before_yesterday_str = day_before_yesterday.strftime('%Y%m%d')
    # datetime format YYYYMMDDHHMMSS
    arxiv_query = f'({subject}) AND ' \
                    f'submittedDate:' \
                    f'[{day_before_yesterday_str}000000 TO {day_before_yesterday_str}235959]'
    articles = arxiv.query(query=arxiv_query,
                            max_results=1000,
                            sort_by='submittedDate',
                            iterative=False)
    results = search_keyword(articles, keywords, score_threshold)

    slack_id = os.getenv("SLACK_ID") or args.slack_id
    line_token = os.getenv("LINE_TOKEN") or args.line_token

    text = f'\n**{dt_now.strftime("%Y-%m-%d")}**\nNum of Articles = {len(results)}'
    send2app(text, slack_id, line_token)

    notion_token = os.getenv("NOTION_TOKEN") or args.notion_token
    notion_database_id = os.getenv("NOTION_DATABASE_ID") or args.notion_database_id
    post2notion(results, dt_now, notion_token, notion_database_id)

if __name__ == "__main__":
    main()
