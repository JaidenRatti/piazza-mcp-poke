FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml README.md ./
COPY src/ src/

RUN uv pip install --system .

ENV TRANSPORT=streamable-http
ENV PORT=8247

EXPOSE 8247

CMD ["piazza-mcp-poke"]
