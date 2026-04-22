import json, os
count = 0
for fn in sorted(os.listdir('test_history'), reverse=True)[:10]:
    with open('test_history/'+fn) as f: d = json.load(f)
    checks = d.get('checks', [])
    pack_check = next((c for c in checks if c.get('name') == 'Пакет подключён'), None)
    if not pack_check:
        continue
    count += 1
    rr = d.get('rawResponses') or {}
    spid = (rr.get('packActivateResult') or {}).get('subscriberPackId','')
    product_id = None
    if 't' in str(spid):
        try: product_id = int(str(spid).split('t',1)[1])
        except: pass

    before_items = (rr.get('rtDiscountsBefore') or {}).get('items', [])
    after_items  = (rr.get('rtDiscountsAfter')  or {}).get('items', [])
    before_pids = set(i.get('productId') for i in before_items)
    after_pids  = set(i.get('productId') for i in after_items)
    new_pids = after_pids - before_pids

    print('FILE:', fn)
    print('  pack:', pack_check.get('expected'))
    print('  subscriberPackId:', spid, '-> productId:', product_id)
    print('  before productIds:', sorted(before_pids))
    print('  after  productIds:', sorted(after_pids))
    print('  NEW productIds:', sorted(new_pids))

    # balance
    bal_b = (rr.get('balanceBeforeActivate') or {}).get('availableBalance')
    bal_a = (rr.get('balanceAfterActivate')  or {}).get('availableBalance')
    print('  balance before=%s after=%s diff=%s' % (bal_b, bal_a, (bal_b-bal_a) if bal_b and bal_a else 'N/A'))
    print()
    if count >= 5:
        break

