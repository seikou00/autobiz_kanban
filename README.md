# AutobizDevOps Kanban

## Python compatibility

All Python scripts support CPython 3.7 and later. The 3.7 compatibility floor
is intended for legacy CI/CD agents and plugin hosts that cannot yet upgrade
their interpreter. Use a currently maintained Python release when the runtime
environment permits it.

The Python code uses only the standard library; no third-party Python packages
need to be installed. Individual workflows may still invoke external project
tools such as Git, Maven, or npm when those tools are part of the task.

Run the compatibility guard with:

```bash
python -m unittest tests.test_python37_compatibility
```
