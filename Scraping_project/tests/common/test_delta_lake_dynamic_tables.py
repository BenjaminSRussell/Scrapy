import pandas as pd

from src.common.delta_lake import DeltaLakeManager


def test_can_write_to_new_table_dynamically(delta_sandbox: DeltaLakeManager):
    """
    Verify that the DeltaLakeManager can write to a new, unregistered table
    by dynamically creating the table path.
    """
    df = pd.DataFrame([{"k": 1, "v": "a"}])
    records = df.to_dict("records")
    table_name = "new_dynamic_table"

    # This should now succeed without raising a ValueError
    delta_sandbox.write(table_name, records, mode="overwrite", async_write=False)

    # Verify that the table now exists in the manager's registry
    assert table_name in delta_sandbox.tables
    assert delta_sandbox.table_exists(table_name)

    # Verify that we can read the data back
    read_data = delta_sandbox.read(table_name)
    assert len(read_data) == 1
    assert read_data[0]["k"] == 1
    assert read_data[0]["v"] == "a"
