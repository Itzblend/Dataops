import os
import sys
import shutil
import requests
import json
import itertools
from datetime import datetime, timedelta
import logging

from src.paths.paths import ETL_DATA_PATH

DEPLOYMENT = os.environ.get("DEPLOYMENT") or "DEV"
DATA_FOLDER = ETL_DATA_PATH
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s-[%(filename)s:%(lineno)s - %(funcName)1s()]-%(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(f'{sys.argv[0].split(".")[0]}.log'),
        logging.StreamHandler(),
    ],
)

from .db import Database


class Filters:
    def __init__(self):
        pass

    def last_updated_filter(
            schema: str, table: str, timestamp_col: str, db_config, database: str, repository_url: str = ''
    ):
        """
        Check the database on corresponding table for the latest timestamp
        """
        db = Database(
            host=db_config["host"],
            port=db_config["port"],
            user=db_config["user"],
            password=db_config["password"],
            database=database,
        )
        with db.connection_cursor() as cur:
            filter_query = f"""
            SELECT COALESCE(MAX({timestamp_col}), '1970-01-01T00:00:00Z') FROM {schema}.{table}
            """
            if repository_url:
                filter_query = filter_query + ' ' + f"""WHERE data ->> 'repository_url' = '{repository_url}'"""
            cur.execute(filter_query)
            timestamp = datetime.strptime(
                cur.fetchone()[0], "%Y-%m-%dT%H:%M:%SZ"
            ) + timedelta(seconds=1)
            timestamp = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

            return timestamp


class Organization:
    def __init__(self, org, config, db_config):
        self.baseurl = config["github"]["baseurl"]
        self.org = org

    def get_issues_endpoint(self, repository):
        return f"{self.baseurl}/repos/{self.org}/{repository}/issues"

    def get_pulls_endpoint(self, repository):
        return f"{self.baseurl}/repos/{self.org}/{repository}/pulls"

    def get_commits_endpoint(self, repository):
        return f"{self.baseurl}/repos/{self.org}/{repository}/commits"

    def get_org_events_endpoint(self, ):
        return f"{self.baseurl}/orgs/{self.org}/events"

    def get_list_repos_entrypoint(self):
        return f"{self.baseurl}/orgs/{self.org}/repos"


class ETL(Organization):
    def __init__(self, org, config, db_config, database=''):
        super().__init__(org, config, db_config)
        self.org = org
        os.makedirs(DATA_FOLDER, exist_ok=True)
        self.config = config
        self.db_config = db_config
        self.database = database

    def fetch_issues(self, save_folder: str, file_prefix="issues"):
        shutil.rmtree(os.path.join(ETL_DATA_PATH, save_folder), ignore_errors=True)
        repos, repo_urls = self.list_org_repos()
        for repo, repo_url in zip(repos, repo_urls):
            url = Organization.get_issues_endpoint(self, repository=repo)

            params = {
                "state": "all",
                "per_page": 100,
                "since": Filters.last_updated_filter(
                    schema="datalake",
                    table="issues_json",
                    timestamp_col="data ->> 'updated_at'",
                    db_config=self.db_config,
                    database=self.database,
                    repository_url=repo_url
                ),
            }
            output_folder = os.path.join(save_folder, repo)
            self.fetch_api(url, params, output_folder, file_prefix)

    def fetch_pulls(self, save_folder: str, file_prefix="pulls"):
        url = Organization.get_pulls_endpoint(self, repository="dbt-core")

        params = {
            "state": "all",
            "per_page": 100,
            # Pulls api doesn't support "since" parameter but let's leave this here in hopes they do
            "since": Filters.last_updated_filter(
                schema="datalake",
                table="pulls_json",
                timestamp_col="data ->> 'updated_at'",
                db_config=self.db_config,
                database=self.database,
            ),
        }

        self.fetch_api(url, params, save_folder, file_prefix)

    def fetch_commits(self, save_folder: str, file_prefix="commits"):
        url = Organization.get_commits_endpoint(self, repository="dbt-core")

        params = {
            "state": "all",
            "per_page": 100,
            "since": Filters.last_updated_filter(
                schema="datalake",
                table="commits_json",
                timestamp_col="(data -> 'commit' -> 'author' ->> 'date')",
                db_config=self.db_config,
                database=self.database,
            ),
        }

        self.fetch_api(url, params, save_folder, file_prefix)

    def fetch_org_events(self, save_folder: str, file_prefix="org_events"):
        url = Organization.get_org_events_endpoint(self)

        params = {
            "state": "all",
            "per_page": 100,
            "since": Filters.last_updated_filter(
                schema="datalake",
                table="org_events_json",
                timestamp_col="data ->> 'created_at'",
                db_config=self.db_config,
                database=self.database,
            ),
        }

        self.fetch_api(url, params, save_folder, file_prefix)

    def list_org_repos(self):
        url = self.get_list_repos_entrypoint()

        resp = requests.get(url=url, auth=("", self.config["github"]["api_token"]))
        data = json.loads(resp.text)
        repositories_list = [repo["name"] for repo in data]
        repository_urls = [repo["url"] for repo in data]
        print(repositories_list)
        return repositories_list, repository_urls

    def fetch_api(self, url, params, save_folder, file_prefix):
        output_data_folder = os.path.join(DATA_FOLDER, save_folder)
        shutil.rmtree(output_data_folder, ignore_errors=True)

        # os.makedirs(save_folder, exist_ok=True)

        output_data_prefix = os.path.join(DATA_FOLDER, save_folder, file_prefix)

        for i in itertools.count():
            try:
                resp = requests.get(
                    url=url,
                    auth=("", self.config["github"]["api_token"]),
                    params=params,
                )
                data = json.loads(resp.text)
                if len(data) > 0:
                    os.makedirs(output_data_folder, exist_ok=True)
                    with open(f"{output_data_prefix}_{i}.json", "a") as out_file:
                        for row in data:
                            out_file.write(json.dumps(row))
                            out_file.write("\n")

                url = resp.links["next"]["url"]
            except KeyError:
                break


if __name__ == "__main__":
    pass
