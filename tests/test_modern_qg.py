from app.modern_qg import build_evidence_batches, build_evidence_units, validate_model_card


def test_evidence_cleaning_removes_pdf_artifacts():
    text = '''
    2026, 23:49 converted-repo.txt file:///C:/Users/jikopl/Downloads/converted-repo.txt 4/5
    Ажурная кристаллическая структура льда приводит к тому, что его плотность меньше плотности жидкой воды.
    file:///C:/bad/path.txt 5/5
    Лёд, будучи легче жидкой воды, образуется на поверхности водоёмов, что препятствует дальнейшему замерзанию воды.
    '''
    units = build_evidence_units(text, desired_count=4, language='ru')
    joined = ' '.join(u.text for u in units).lower()
    assert units
    assert 'file:///' not in joined
    assert 'converted-repo' not in joined
    assert any('плотность' in u.text.lower() for u in units)


def test_prompt_builder_is_safe_json():
    text = 'Лёд, будучи легче жидкой воды, образуется на поверхности водоёмов, что препятствует дальнейшему замерзанию воды.'
    batches = build_evidence_batches(text, desired_count=1, language='ru')
    assert batches
    assert 'EVIDENCE_JSON' in batches[0].prompt
    assert 'file:///' not in batches[0].prompt


def test_validator_rejects_bad_templates_and_accepts_supported_card():
    evidence = build_evidence_units('Лёд, будучи легче жидкой воды, образуется на поверхности водоёмов, что препятствует дальнейшему замерзанию воды.', desired_count=1)
    bad, reason = validate_model_card({'front': 'Что такое воды?', 'back': 'Лёд легче жидкой воды.', 'source_quote': evidence[0].text, 'mnemonic': 'Лёд легче воды.'}, evidence)
    assert bad is None
    assert reason
    good, reason = validate_model_card({'front': 'Почему лёд образуется на поверхности водоёмов?', 'back': 'Лёд легче жидкой воды, поэтому держится на поверхности и замедляет дальнейшее замерзание.', 'source_quote': evidence[0].text, 'mnemonic': 'Лёд как крышка, которая лежит сверху воды.'}, evidence)
    assert good is not None, reason
    assert good['front'].endswith('?')


def test_batches_cover_requested_count_even_with_few_evidence_units():
    text = " ".join([
        "Лёд используют в строительстве иглу в приполярных регионах.",
        "Ледяная кровь удлиняет время спасения пострадавшего с 10—15 до 30—45 минут.",
        "Ледяная гидросмесь в 5—7 раз эффективнее простой холодной воды в системах охлаждения зданий.",
        "Лёд может содержать твёрдые частицы, капельки растворов и пузырьки газа.",
        "Основные запасы льда сосредоточены в полярных шапках Антарктиды и Гренландии.",
        "Известны три аморфных разновидности и 17 кристаллических модификаций льда.",
    ])
    batches = build_evidence_batches(text, desired_count=13, language='ru', batch_card_limit=8)
    assert batches
    assert sum(b.count for b in batches) == 13


def test_validator_repairs_paraphrased_source_quote_from_evidence():
    evidence = build_evidence_units('Ледяная гидросмесь в 5—7 раз эффективнее простой холодной воды в системах охлаждения зданий.', desired_count=1)
    good, reason = validate_model_card({
        'front': 'Чем ледяная гидросмесь лучше простой холодной воды?',
        'back': 'Она эффективнее простой холодной воды в системах охлаждения зданий в 5—7 раз.',
        'source_quote': 'гидросмесь эффективнее воды',
        'mnemonic': 'Гидросмесь как усиленная охлаждающая жидкость.'
    }, evidence)
    assert good is not None, reason
    assert '5—7' in good['source_quote']
