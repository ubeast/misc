# to-markdown

Print a `pandas` DataFrame as a GitHub-flavored Markdown table.

One file: [`to_markdown.py`](to_markdown.py). A standalone helper — **not**
stdlib only (it takes a `pandas.DataFrame`), and not part of any other tool
here.

```python
from to_markdown import to_markdown

to_markdown(df)                 # first 5 rows
to_markdown(df, record_count=20)
```

Pipe values are escaped; `NaN` renders as an empty cell. Output goes to stdout.
