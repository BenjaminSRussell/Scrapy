# Stage 1: Build the project with dependencies
FROM python:3.10-slim as builder

WORKDIR /app

# Install poetry
RUN pip install poetry

# Copy only the files necessary for installing dependencies
COPY pyproject.toml poetry.lock* ./

# Install dependencies
# We need to handle the case where poetry.lock doesn't exist
RUN poetry install --without dev --no-root

# Stage 2: Create the final image
FROM python:3.10-slim

WORKDIR /app

# Copy the installed dependencies from the builder stage
COPY --from=builder /app/.venv /.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy the rest of the application code
COPY . .

# Command to run the pipeline
CMD ["python", "run_pipeline.py"]
