# The locked-down box where the agent's Python code runs (no internet at run time).
FROM python:3.12-slim
# openpyxl: Excel files (pandas .to_excel / read_excel); python-docx: Word files (import docx)
RUN pip install --no-cache-dir pandas numpy matplotlib openpyxl python-docx
WORKDIR /work
