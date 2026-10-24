from app.services.textutil import fingerprint_hash, normalize_subject, redact, sanitize_html, trigram_similarity


def test_amounts_dates_and_ids_collapse_to_one_template():
    first = normalize_subject("INR 2,450.00 debited from A/c XX9012 via UPI on 21 Oct")
    second = normalize_subject("INR 18.00 debited from A/c XX1144 via UPI on 3 Jan 2026")
    assert first == second
    assert "{amount}" in first
    assert "{date}" in first
    assert "2450" not in first
    assert fingerprint_hash("kotak.com", first) == fingerprint_hash("kotak.com", second)


def test_re_prefix_and_names_are_removed():
    template = normalize_subject("Re: Interview with Priya Sharma on 02/11/2026", names=["Priya Sharma"])
    assert template.startswith("interview")
    assert "{name}" in template
    assert "priya" not in template


def test_different_merchants_stay_different():
    a = normalize_subject("INR 10 debited via UPI to Swiggy")
    b = normalize_subject("INR 10 debited via UPI to Zomato")
    assert a != b


def test_redact_removes_phone_roll_and_long_ids():
    raw = "Roll 1MS21CS001 called +91 98450 11223 about account 123456789012"
    cleaned = redact(raw)
    assert "98450" not in cleaned
    assert "1MS21CS001" not in cleaned
    assert "123456789012" not in cleaned
    assert "[phone]" in cleaned or "[id]" in cleaned


def test_remote_images_and_scripts_are_stripped():
    html = '<p onclick="evil()">Hi</p><script>alert(1)</script><img src="https://spy.example/t.png"><a href="javascript:alert(1)">x</a><a href="https://ok.example">ok</a>'
    safe = sanitize_html(html)
    assert "<script" not in safe
    assert "onclick" not in safe
    assert "spy.example" not in safe
    assert "javascript:" not in safe
    assert "https://ok.example" in safe
    assert "remote image blocked" in safe


def test_fraud_subject_is_not_similar_to_a_debit_template():
    debit = normalize_subject("INR 2,450.00 debited from A/c XX9012 via UPI")
    fraud = normalize_subject("Alert: suspicious transaction blocked on your debit card")
    assert trigram_similarity(debit, fraud) < 0.6
