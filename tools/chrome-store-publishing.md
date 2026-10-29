# Chrome Web Store publishing: one-time setup

## What this does

- Each release on main checks the Voice Companion Browser Connection in the Chrome Web Store.
- If the release has a higher extension version, it uploads the extension and sends it for review.
- After Google approves it, Chrome updates every user's copy on its own. Users do nothing.
- A daily check does the same, in case a review was still running at release time.
- Until this setup is done, releases still work. The store step is skipped, and a note on the build says why.

You do this setup once. It takes about 20 minutes. A helper can do it with you.

## Before you start

- Use the Google account that owns the Voice Companion Browser Connection in the Chrome Web Store.
- The commands below run in Google Cloud Shell. It is a command line inside the browser, already signed in to your Google account. Nothing needs to be installed.
- Paste one command at a time and press Enter. Wait for it to finish before the next one.
- If Cloud Shell restarts, run the three lines in step 2 of Part 1 again. They set names the later commands use. If you are past step 3 of Part 4, run that step again too.
- Links:
  - [Google Cloud Shell](https://shell.cloud.google.com/?show=terminal)
  - [Chrome Web Store developer dashboard](https://chrome.google.com/webstore/devconsole)
  - [Google's guide to service accounts for the Chrome Web Store](https://developer.chrome.com/docs/webstore/service-accounts)
  - [Google's guide to Workload Identity Federation with GitHub](https://cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines)

## Part 1: Make the Google Cloud project and turn on the API

1. Open Google Cloud Shell from the link above. If it asks you to agree to the terms, agree, then choose Start Cloud Shell. Wait until you hear or see a command prompt that ends with a dollar sign.
2. Paste these three lines, one at a time. The project ID must be new across all of Google. If step 3 says the ID is taken, change the number at the end and run these three lines again.

   ```
   PROJECT_ID=voice-companion-store-4821
   ACCOUNT=chrome-store-publisher@$PROJECT_ID.iam.gserviceaccount.com
   REPO=willwalsh14-hub/voice-companion-updates
   ```

3. Make the project:

   ```
   gcloud projects create $PROJECT_ID --name="Voice Companion Store"
   ```

4. Make it the project Cloud Shell works in:

   ```
   gcloud config set project $PROJECT_ID
   ```

5. Turn on the Chrome Web Store API, and the services that let GitHub sign in without a key:

   ```
   gcloud services enable chromewebstore.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com
   ```

   If it says billing is needed, it is not. These services are free. Run the command again after a minute.

## Part 2: Make the service account

A service account is a Google account for a program, not a person. The release uses it to reach the store.

1. Make it. Give it no roles: the Chrome Web Store page in Part 3 is what lets it in.

   ```
   gcloud iam service-accounts create chrome-store-publisher --display-name="Chrome Web Store publisher"
   ```

2. Show its email address, to check it worked:

   ```
   echo $ACCOUNT
   ```

   It reads like chrome-store-publisher@voice-companion-store-4821.iam.gserviceaccount.com. You need it in Part 3.

## Part 3: Add the service account in the Chrome Web Store

1. Open the Chrome Web Store developer dashboard from the link above.
2. If you belong to more than one publisher, switch to the one that owns the Voice Companion Browser Connection.
3. Go to the Account page.
4. Find the service account section. Paste the service account email from Part 2. Save.
   - A publisher can have only one service account.
5. Find your publisher ID. Google's guide says it is under Publisher, then Settings. Copy it. You need it in Part 5.

## Part 4: Let GitHub sign in without a key

This makes a "workload identity pool". GitHub proves which repository and branch is asking. Google lets in only the main branch of willwalsh14-hub/voice-companion-updates. No password or key is stored anywhere.

1. Make the pool:

   ```
   gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
   ```

2. Make the GitHub provider inside the pool. This is one long line; paste it whole.

   ```
   gcloud iam workload-identity-pools providers create-oidc voice-companion --location=global --workload-identity-pool=github --display-name="Voice Companion releases" --issuer-uri="https://token.actions.githubusercontent.com" --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" --attribute-condition="assertion.repository=='$REPO' && assertion.ref=='refs/heads/main'"
   ```

3. Look up the project number. It is a different number from the project ID.

   ```
   PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
   ```

4. Let releases from the repository act as the service account:

   ```
   gcloud iam service-accounts add-iam-policy-binding $ACCOUNT --role=roles/iam.workloadIdentityUser --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"
   ```

5. Show the provider name. You need it in Part 5.

   ```
   echo projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/voice-companion
   ```

## Part 5: Give the three settings to GitHub

None of these is a secret. They are names, not passwords.

1. On GitHub, open the repository settings page: [Actions variables for voice-companion-updates](https://github.com/willwalsh14-hub/voice-companion-updates/settings/variables/actions).
2. Choose New repository variable. Name: CWS_PUBLISHER_ID. Value: the publisher ID from Part 3. Choose Add variable.
3. Choose New repository variable. Name: CWS_SERVICE_ACCOUNT. Value: the service account email from Part 2. Choose Add variable.
4. Choose New repository variable. Name: CWS_WORKLOAD_IDENTITY_PROVIDER. Value: the line from Part 4, step 5. It starts with projects/ and ends with providers/voice-companion. Choose Add variable.

If you prefer the GitHub command line on your computer, these three commands do the same. Put your own values between the quotes.

```
gh variable set CWS_PUBLISHER_ID --repo willwalsh14-hub/voice-companion-updates --body "YOUR PUBLISHER ID"
gh variable set CWS_SERVICE_ACCOUNT --repo willwalsh14-hub/voice-companion-updates --body "YOUR SERVICE ACCOUNT EMAIL"
gh variable set CWS_WORKLOAD_IDENTITY_PROVIDER --repo willwalsh14-hub/voice-companion-updates --body "projects/NUMBER/locations/global/workloadIdentityPools/github/providers/voice-companion"
```

## Part 6: Check that it works

1. Run the daily store check now, with the GitHub command line on your computer:

   ```
   gh workflow run chrome-store-check.yml --repo willwalsh14-hub/voice-companion-updates
   ```

   Or open the [Chrome Web Store check workflow page](https://github.com/willwalsh14-hub/voice-companion-updates/actions/workflows/chrome-store-check.yml) and choose Run workflow.
2. Wait about two minutes. Then read the result:

   ```
   gh run view --repo willwalsh14-hub/voice-companion-updates $(gh run list --repo willwalsh14-hub/voice-companion-updates --workflow chrome-store-check.yml --limit 1 --json databaseId --jq ".[0].databaseId")
   ```

3. The result is one note, named Chrome Web Store, then one of these:
   - submitted for review: it worked. Google reviews the new version, often within a few days.
   - skipped: nothing needed doing, or a setting is missing. The note names what is missing.
   - not sent: the store or sign-in refused. The note gives Google's reason.

Ask Claude to read the note for you if it is unclear.

## If your organization blocks keyless sign-in: use a key instead

Keyless sign-in in Part 4 is safer. Use a key only if Part 4 cannot be done.

1. In Cloud Shell, make a key file:

   ```
   gcloud iam service-accounts keys create key.json --iam-account=$ACCOUNT
   ```

   If it says key creation is disabled by an organization policy, keys are blocked for this project. Use Part 4 instead.
2. Show the key file, then copy all of it:

   ```
   cat key.json
   ```

3. On GitHub, open [Actions secrets for voice-companion-updates](https://github.com/willwalsh14-hub/voice-companion-updates/settings/secrets/actions). Choose New repository secret. Name: CWS_SERVICE_ACCOUNT_KEY. Paste the whole key as the value. Choose Add secret.
4. Delete the key file from Cloud Shell:

   ```
   rm key.json
   ```

5. You still need the CWS_PUBLISHER_ID variable from Part 5. The other two variables are not needed with a key.

## Good to know

- The release never fails because of the store. The store step runs after the release is public.
- The store refuses a version it already has. So every change to the extension raises its version in manifest.json. A check on each pull request makes sure of that.
- The store also refuses a "key" line in manifest.json. The upload leaves it out. The copy in the installer keeps it, so the extension ID stays the same for testers who loaded it by hand.
- If you change who can see the item in the developer dashboard, publish it once by hand there. Google says the API cannot publish until you do.
- On users' computers: when Voice Companion sees an older extension, it asks Chrome to check the store at once. The user hears once: "The Chrome extension is updating. Chrome checks for the new version on its own; it may take a little while."
