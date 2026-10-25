# PlanBridge

PlanBridge is an explainable project-progress capture and schedule-linking
system developed as an eight-week solo MVP inspired by SIH26122.

## Problem

Progress reports often describe completed or delayed work in unstructured
language. Linking those statements to planned schedule activities requires
interpretation and can introduce errors.

## MVP capabilities

- Import planned activities and progress reports from CSV/XLSX files.
- Preserve original report text and extract structured progress events.
- Rank candidate schedule activities using explainable text matching.
- Route ambiguous matches to human review.
- Apply approved changes transactionally.
- Maintain an audit trail of approved changes.
- Display project progress summaries.
- Evaluate matching quality on a held-out synthetic test set.

## Technology stack

- Frontend: React, Vite, TypeScript
- Backend: Python, FastAPI
- Database: PostgreSQL, SQLAlchemy, Alembic
- Data ingestion: pandas, openpyxl
- Matching: RapidFuzz, scikit-learn TF-IDF
- Testing: pytest, HTTPX
- Version control: Git, GitHub

## Safety and scope

Original report text must be preserved. Inputs must be validated.
Ambiguous matches require human review, and schedule changes must not be
applied without approval. Approved changes must have audit history.

Version 1.0 excludes OCR, speech recognition, live enterprise integrations,
autonomous schedule changes, complex predictive ML, multi-agent AI, and
microservices.

## Development status

Week 1: Repository foundation and initial project/data contract.
