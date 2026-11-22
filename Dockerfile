FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /bot
COPY bot.py geometry.py physics.py mechfight.py combat_math.py /bot/
ENTRYPOINT ["python", "-u", "/bot/bot.py"]
