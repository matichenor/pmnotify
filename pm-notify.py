from github import Github
import os
import sys
import sqlite3
import requests
import time
import logging
from datetime import datetime

# Constants
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
ENV_SLACK_WEBHOOK_URL = "SLACK_WEBHOOK_URL"
EMPLOYEE_ORG_NAME = "polygon-io"
SLACK_CHANNEL_NAME = "#product-notifications"

class NotifyDb:
    def __init_tables( self ):
        # If they tables don't exist yet, they sure will after this runs.
        self.__cur.execute("""CREATE TABLE IF NOT EXISTS lastseen(
        source TEXT PRIMARY KEY,
        lastseen TEXT);
        """)
        self.__conn.commit()


    def __init__ ( self ):
        self.__conn = sqlite3.connect('pmnotify.db')
        self.__cur = self.__conn.cursor()

    # Keep track of the last processed message in the db
    def update_lastseen( self, source, lastseen):
        update_sql = "INSERT OR REPLACE INTO lastseen ('source', 'lastseen') VALUES ('{0}', '{1}')".format(source, lastseen)
        self.__cur.execute(update_sql)
        self.__conn.commit()
    
    # Get the lastseen value for the specified datasource.  Lastseen is just a string
    #   so it's up to the caller to determine how to parse it.
    def get_lastseen( self, source ):
        select_sql = "SELECT lastseen FROM lastseen WHERE source='{0}'".format(source)
        retval = ""
        self.__cur.execute(select_sql)

        first = self.__cur.fetchone()
        if first and first is not None:
            retval = first[0]

        return retval
            

class GithubIngestor:
    def __init__( self, db):
        github_token = os.environ.get(ENV_GITHUB_TOKEN)
        if github_token is None:
            print("Error! Missing required environment variable {0}".format(ENV_GITHUB_TOKEN))
            raise Exception("Unable to intialize github connection, missing required github token.")
        self.__db = db

        try:
            self.__github = Github(github_token)
        except Exception as e:
            print ("Failed to connect to github!")
            print ("Exception: {0}".format(e))
            raise e

    # get_repo_issues
    #
    # Get all issues from a specified github repo.
    #
    def get_repo_issues( self, repo_name ):
        get_all_issues_query = "repo:{0}".format(repo_name)
        time.sleep(1)   # sleep for 1s or github gets cranky and fails rate limit errors
        resp = self.__github.search_issues(query=get_all_issues_query)
        return resp

    # get_repo_recent_issues
    #
    # Get new issues raised since the last observed issue for the specified repo.
    #   Utilizes a local sqlite db to keep track of observed items.
    #
    def get_repo_recent_issues( self, repo_name ):
        lastseen = self.__db.get_lastseen(repo_name)

        # test override value
        #lastseen = "2022-03-01T14:55:01"  
        most_recent_issues_query = "repo:{0}".format(repo_name)

        if lastseen is not None and lastseen != "":
            most_recent_issues_query = most_recent_issues_query + " created:>{0}".format(lastseen)
        try:
            time.sleep(1)   # sleep for 1s or github gets cranky and fails rate limit errors 
            resp = self.__github.search_issues(query=most_recent_issues_query)
        except Exception as e:
            print ("Error! Got Exception on github query. e={0}".format(e))
            raise e
        
        return resp
    
    def update_latest_issue( self, issues, repo_name ):
        latest_time = None
        for issue in issues:
            if latest_time is None:
                latest_time = issue.created_at
            elif issue.created_at and issue.created_at > latest_time:
                latest_time = issue.created_at

        if latest_time is not None:
            latest_time_str = latest_time.isoformat()
            self.__db.update_lastseen( repo_name, latest_time_str)

    # get_slack_message_from_issue
    #
    # Convert an issue into a slack notification message
    #
    def get_slack_message_from_issue( self, issue, repo_name):
        # I declare these variables not because I need to, but because I choose to.
        #   Brevity be damned!
        title = issue.title
        issue_url = issue.html_url
        issue_creator = issue.user.login
        body = issue.body

        # Check if a user is an employee or external
        is_employee = False
        try:
            org_check_resp = issue.user.get_organization_membership(EMPLOYEE_ORG_NAME)
            is_employee = True
        except Exception as e:
            logging.debug("Determined that {0} is not an {1} employee".format(issue_creator,EMPLOYEE_ORG_NAME))

        if is_employee:
            slack_text = ":github_octocat: New internal issue {0} raised by employee {1}\n{2}\n{3}".format(repo_name, issue_creator, title, issue_url)
        else:
            slack_text = ":person_in_tuxedo: New customer issue in {0} raised by *{1}*\n{2}\n{3}".format(repo_name, issue_creator, title, issue_url)

        return slack_text

    def get_recent_issues_slack_messsages( self, repo_name ):
        issues = self.get_repo_recent_issues(repo_name)
        messages = []

        for issue in issues:
            messages.append(self.get_slack_message_from_issue(issue, repo_name))

        self.update_latest_issue( issues, repo_name )

        return messages

    def getSummarySlackMessage( self ):
        print ("not implemented yet")

    def get_public_repos( self ):
        # TODO: this query currently returns all public repos.  The syntax should
        #   be updated to explicitly only return public repos
        repos = self.__github.search_repositories(query="org:polygon-io")
        return repos

class SlackBot:
    def __init__ ( self, slack_websocket_url, db ):
        self.__slack_websocket_url = slack_websocket_url
        self.__db = db
        self.__channel_name = SLACK_CHANNEL_NAME

    def update_last_post_time( self, appendix="" ):
        timestamp = datetime.now().strftime("%Y%m%d")
        self.__db.update_lastseen( self.__channel_name + appendix, timestamp)

    def get_last_post_time(self, appendix=""):
        return self.__db.get_lastseen(self.__channel_name + appendix)
    
    def is_last_post_within_24h(self, appendix=""):
        last_date_str = self.get_last_post_time(appendix)
        now_date_str = timestamp = datetime.now().strftime("%Y%m%d")
        try:
            diff = int(now_date_str) - int(last_date_str)
        except Exception as e:
            print("Failed to deterimine if a message was posted in the past 24h.  We're gonna assume no.")
            return False
        return diff <= 0

    def post_daily_message(self, message, appendix):
        if self.is_last_post_within_24h(appendix):
            print("Skipping status message [[{0}]] because we already posted on in the last 24h".format(message))
            return    
        self.post_message_to_channel(message, appendix)

    def post_message_to_channel( self,  message, appendix="" ):
        slack_headers = {'Content-type': 'application/json'}
        slack_post_data = {'text': message}

        # Todo - error handling 
        r = requests.post(self.__slack_websocket_url, json=slack_post_data, headers=slack_headers)

        # Update the db with the last time we sent a message.  We may want to rate limit our messages using this data.
        self.update_last_post_time(appendix)


# Initialize a local sqlitedb 
db = NotifyDb()

# Connect to Slack via a webhook URL https://api.slack.com/messaging/webhooks
slack_webhook_url = os.environ.get(ENV_SLACK_WEBHOOK_URL)
if slack_webhook_url is None:
    print("Error! Missing required environment variable {0}".format(ENV_SLACK_WEBHOOK_URL))
    sys.exit()
slack = SlackBot(slack_webhook_url, db)

# Fetch a list of all public repos from githhub
github_ingestor = GithubIngestor(db)
repos = github_ingestor.get_public_repos()

# Index through each public repo and check if any new Issues have been raised.
#   Post a slack message with summary data of each new Issue.
no_update_repos = []
for repo in repos:
    repo_name = repo.full_name
    messages = github_ingestor.get_recent_issues_slack_messsages(repo_name)

    if len(messages) == 0:
        no_update_repos.append(repo_name)
        continue 

    for message in messages:
        slack.post_message_to_channel(message)

# Post a slack message confirm the job ran and summarizing results.     
job_done_message = "Finished checking {0} github repos for new issues. {1}/{2} repos contained no new issues.".format(repos.totalCount, len(no_update_repos), repos.totalCount)
slack.post_daily_message(job_done_message, "job_done")

# So long, farewell, auf wiedersehen, good night!