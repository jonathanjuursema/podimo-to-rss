#!/bin/bash

set -e

echo "Starting app..."
uvicorn main:api --host 0.0.0.0 --port 80