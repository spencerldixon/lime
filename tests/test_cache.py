from lime.cache import ImageCache


def test_byte_limit_evicts_the_least_recently_used_entry():
    cache = ImageCache(max_bytes=8)
    cache.put("first", b"1234", 4)
    cache.put("second", b"5678", 4)
    assert cache.get("first") == b"1234"
    cache.put("third", b"abcd", 4)
    assert cache.get("second") is None
    assert cache.get("first") == b"1234"
    assert cache.get("third") == b"abcd"


def test_entry_limit_also_bounds_empty_values():
    cache = ImageCache(max_entries=2)
    for key in ("first", "second", "third"):
        cache.put(key, (), 0)
    assert cache.get("first") is None
    assert cache.get("second") == cache.get("third") == ()


def test_oversized_values_are_not_retained_or_evict_other_entries():
    cache = ImageCache(max_bytes=4)
    cache.put("small", b"1234", 4)
    cache.put("large", b"12345", 5)
    assert cache.get("small") == b"1234"
    assert cache.get("large") is None


def test_replacing_an_entry_releases_its_previous_size():
    cache = ImageCache(max_bytes=8)
    cache.put("first", b"1234", 4)
    cache.put("first", b"12", 2)
    cache.put("second", b"345678", 6)
    assert cache.get("first") == b"12"
    assert cache.get("second") == b"345678"
