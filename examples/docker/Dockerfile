FROM python:3.12-slim

WORKDIR /code
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
# selfhealing is auto-installed from pyproject.toml dependencies

COPY . .
ENV PYTHONPATH=/code

CMD ["gunicorn", "myproject.wsgi:application", "-c", "gunicorn.conf.py"]
