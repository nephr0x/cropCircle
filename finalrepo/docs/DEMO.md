# Demo script

About four minutes, two browser tabs. Each tab has its own sign-in, so a farmer and a researcher can be open side by side.

## Before you start

1. `uvicorn app:app --reload`, then open <http://localhost:8000> twice.
2. Tab 1: sign in as the farmer (`555 010 0001`, PIN `1234`).
3. Tab 2: switch to Researcher and sign in (`okafor@demo.farmline`, `demo1234`).
4. Run the demo case once beforehand. Results are saved, so a case you have already run reopens instantly if the network misbehaves on the day.

## The run

**1. The ask (30 seconds).** In the farmer tab, click **Fill in the demo example**: sorghum near Lubbock, sticky shiny leaves, tiny yellow bugs, spreading fast. Point out the collapsed details line and the speak button. Click **Find help**.

**2. The answer (90 seconds).** The summary lands first, then each section fills in with a status label. Walk through **What to do first**, then **Suggested solution**, and scroll to **Sources used**. Open **Real cases from the web**.

Say the labels out loud. "Live AI" means it was written just now. "Saved answer" means the live step did not finish and the saved demo answer is being shown. Being honest about that is part of the product.

**3. The people (60 seconds).** Show **Farmers who solved this**, then **Experts you can contact** and the drafted message. Under **Researchers on FarmLine**, click **Send my case to Dr. Sam Okafor**.

**4. The loop (45 seconds).** Switch to the researcher tab. The case is at the top, marked "Sent to you by the farmer", and it arrived without a refresh. Click **Write a reply**, choose **Offer to test a solution on this field**, and send. Switch back to the farmer tab: the Messages tab shows a new-message count, and the reply is there.

**5. The close (15 seconds).** Mark the case solved in **My requests** and say that it is now the answer for the next farmer with the same problem.

## If something breaks

- A section shows an error: carry on talking and come back to it. The other sections load on their own.
- The whole app is unreachable: present from the screenshots in `docs/screenshots/`.
- Judges ask what is real: the research papers and extension articles are real; the starting farmers and researchers are demo accounts and are labelled as such on screen.
