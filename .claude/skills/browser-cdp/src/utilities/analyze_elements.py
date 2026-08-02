import json

data = json.load(open('./temp_data/elements.json'))

for d in data:
    text = d.get('text', '')
    tag = d.get('tag', '')
    attrs = d.get('attributes', {})
    class_attr = attrs.get('class', '')
    id_attr = attrs.get('id', '')
    
    if 'result' in text.lower() or 'c-container' in class_attr or 'result' in class_attr:
        print(f'{d["index"]}: {tag} class={class_attr[:50]} id={id_attr} text={text[:80]}')