# pmnotify

## Descrition
A simple script that scans public github repos for new issues.  Posts a message to slack for all newly created issues.

## Configuration

**Environment Variables**
- Define `GITHUB_TOKEN` as a [personal github auth token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token)
- Define `SLACK_WEBHOOK_URL` as a [slack webhook url](https://api.slack.com/messaging/webhooks)

## Todo
* Make repo input handler more generic
* Add a user store, allow a user to subscribe to specific repos
* Add more input mechanisms
