import re
text = open('temp/raw_page_text.txt', encoding='utf-8').read()
# Search for 页面 followed by price
matches = list(re.finditer(r'页面(\d+\.\d+)-([+-]\d+\.\d+)-([+-]\d+\.\d+)%', text))
print('Found', len(matches), 'matches for 页面:')
for i, m in enumerate(matches):
    print(f'  {i}: pos={m.start()}, groups={m.groups()}, context={text[max(0,m.start()-50):m.end()+50]}')