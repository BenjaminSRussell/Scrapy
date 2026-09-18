import pandas as pd

from src.common.delta_lake import DeltaLakeManager

def test_can_write_to_new_table_dynamically(delta_sandbox: DeltaLakeManager):
    df = pd.DataFrame([{"k": 1, "v": "a"}])
    records = df.to_dict("records")
    table_name = "new_dynamic_table"

    delta_sandbox.write(table_name, records, mode="overwrite", async_write=False)

    assert table_name in delta_sandbox.tables
    assert delta_sandbox.table_exists(table_name)

    read_data = delta_sandbox.read(table_name)
    assert len(read_data) == 1
    assert read_data[0]["k"] == 1
    assert read_data[0]["v"] == "a"
