def test_redis_fakeredis_default(redis_client):
    # Should be a fake client; no sockets involved
    redis_client.set("k", "v")
    assert redis_client.get("k") == "v"
