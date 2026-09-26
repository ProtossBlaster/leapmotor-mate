"""Exercise place configuration through the real web routes on synthetic databases."""
import pytest
from test_charging_places import store, closed, row

pw=pytest.importorskip('playwright.sync_api')


@pytest.mark.parametrize('width',[390,1100])
def test_place_forms_and_manual_assignment_in_browser(store,width,tmp_path):
    import main
    import db_reader as W
    from starlette.testclient import TestClient
    W.set_setting('setup_complete','1')
    W.set_setting('language','en')
    client=TestClient(main.app,base_url='http://mate.test')
    cid=closed(store,cost=7,cost_manual=1)
    with pw.sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':width,'height':900})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def serve(route):
            req=route.request
            if not req.url.startswith('http://mate.test/'):
                return route.fulfill(status=204)
            r=client.request(req.method,req.url,content=req.post_data,
                             headers={'content-type':req.headers.get('content-type','')})
            route.fulfill(status=r.status_code,body=r.content,
                          headers={'content-type':r.headers.get('content-type','text/html')})
        page.route('**/*',serve)
        page.goto('http://mate.test/costs')
        page.locator('#charging-places summary').click()
        form=page.locator('#new-place')
        page.locator('#charging-place-map').click(position={'x':100,'y':100})
        assert form.locator('[name=latitude]').input_value()
        assert form.locator('[name=longitude]').input_value()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(tmp_path/f'mate288-{width}.png'),full_page=True)
        form.locator('[name=name]').fill('Weekend house')
        form.locator('[name=latitude]').fill('46')
        form.locator('[name=longitude]').fill('9')
        form.locator('[name=rate]').fill('0.25')
        form.locator('button[type=submit]').click()
        page.wait_for_load_state()
        assert len(W.charging_places_context()['charging_places'])==1
        page.goto(f'http://mate.test/api/charges/{cid}/place')
        # The partial alone has no htmx script; exercise the actual posted endpoint below.
        assert page.locator('option').count()==2
        pid=W.charging_places_context()['charging_places'][0]['id']
        r=client.post(f'/api/charges/{cid}/place',data={'place_id':pid})
        assert r.status_code==204
        assert row(store,cid)['cost']==7
        assert row(store,cid)['charging_place_name']=='Weekend house'
        assert errors==[]
        browser.close()
