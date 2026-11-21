# Contributing

Thanks for looking. FarmLine was built at a hackathon, so there is plenty to improve.

## Getting set up

```bash
pip install -r requirements.txt
cp .env.example .env     # optional: add an AI key to turn on the AI features
uvicorn app:app --reload
```

Then `python check.py` to see which outside services are working for you.

## Before you open a pull request

- Run the app and click through both sides: Find help, a researcher reply, Messages.
- Run `python check.py` if you touched anything that talks to an outside service.
- Delete `farmline.db` and start the app again to confirm a fresh install still works.
- Never commit a real API key. `.env` is ignored; `.env.example` holds the empty template.

## House rules for the code

- Plain Python and plain JavaScript. No build step, and no new dependency unless it earns its place.
- Any advice shown to a farmer must carry its sources, and must never include chemical doses or mixing rates.
- A link the AI was not actually given is dropped before it reaches the screen. Keep that check in place.
- Say plainly on screen when an answer is a saved demo answer rather than live AI.

## Good first issues

- More `sources.txt` entries for other crops and regions
- Importing a real farmer help-line dataset with `library.py add-csv`
- Testing the phone line on a live Twilio number
- Photo diagnosis: let a farmer attach a picture of the leaf
- Outbreak alerts when similar cases cluster in one area
