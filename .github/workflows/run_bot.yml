name: ICT XAUUSD 15M Alert Bot

on:
  schedule:
    # Runs Mon-Fri at :01, :16, :31, :46 during London (07-10 UTC) & NY (12-15 UTC) Killzones
    - cron: '1,16,31,46 7-10,12-15 * * 1-5'
  workflow_dispatch:
    inputs:
      send_test:
        description: 'Send a test Telegram message?'
        required: false
        default: 'true'

jobs:
  run-bot:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install requests pandas numpy

      - name: Run ICT XAUUSD Bot
        env:
          TWELVE_DATA_API_KEY: ${{ secrets.TWELVE_DATA_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          SEND_TEST_MSG: ${{ github.event.inputs.send_test || 'false' }}
        run: python ict_xauusd_bot.py
