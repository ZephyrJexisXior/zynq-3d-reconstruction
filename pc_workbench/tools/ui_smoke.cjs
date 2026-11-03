const base=process.env.STUDIO_URL||'http://127.0.0.1:8790';
// Run with Node + Playwright available through NODE_PATH. Uses the real local UI.
const {chromium}=require('playwright');
const fs=require('fs');const path=require('path');const assert=require('assert');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,args:['--use-angle=swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});const errors=[];
 page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(base+'/');await page.waitForFunction(()=>document.querySelector('#checks').children.length>=2);
 await page.screenshot({path:path.join(out,'startup.png'),fullPage:true});
 const bad=await page.request.post(base+'/api/import',{data:{path:'C:/'}});assert.equal(bad.status(),403);
 await page.locator('#folder').fill(process.argv[2]);
 const imported=page.waitForResponse(r=>r.url().endsWith('/api/import')&&r.request().method()==='POST');
 await page.locator('#import').click();const newId=(await (await imported).json()).id;
 await page.waitForFunction(id=>localStorage.getItem('studio-job')===id,newId);
 await page.waitForFunction(()=>document.querySelectorAll('#directions .view-card').length===4,{},{timeout:45000});
 if(process.argv[3]==='raw'){
   const choices={front:'images/front/position_000.png',left:'images/front/position_006.png',back:'images/front/position_011.png',right:'images/front/position_015.png'};
   for(const [d,label] of Object.entries(choices))await page.locator('#select-'+d).selectOption({label});
 }
 await page.locator('#segment').click();
 await page.waitForFunction(()=>!document.querySelector('#reviewed').disabled,{},{timeout:90000});
 await page.waitForFunction(()=>[...document.querySelectorAll('.view-card img')].every(im=>im.src.includes('/masks/')&&im.complete&&im.naturalWidth>0),{},{timeout:30000});
 await page.screenshot({path:path.join(out,process.argv[3]==='raw'?'raw36_masks.png':'verified_inputs.png'),fullPage:true});
 if(process.argv[3]!=='raw'){
   await page.locator('#reviewed').check();await page.locator('#generate').click();
   await page.waitForFunction(()=>document.querySelector('#phase').textContent.includes('GPU')||document.querySelector('#phase').textContent.includes('权重'),{},{timeout:45000});
   await page.screenshot({path:path.join(out,'running.png'),fullPage:true});
   await page.waitForFunction(()=>document.querySelector('#phase').textContent.includes('建模完成')||document.querySelector('#phase').textContent.includes('处理失败'),{},{timeout:240000});
   assert((await page.locator('#phase').innerText()).includes('建模完成'),await page.locator('#logs').innerText());
   await page.locator('#viewer').scrollIntoViewIfNeeded();
   await page.waitForFunction(()=>document.querySelector('#viewer').loaded===true,{},{timeout:45000});
   await page.locator('#viewer').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'generated_model_ui.png')});
 }
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,process.argv[3]==='raw'?'ui_raw_test.json':'ui_gpu_test.json'),JSON.stringify({page_errors:errors,unauthenticated_post_rejected:true,phase:await page.locator('#phase').innerText(),elapsed:await page.locator('#elapsed').innerText(),job:await page.evaluate(()=>localStorage.getItem('studio-job')),gpu:await page.locator('#gpu').innerText()},null,2));
 console.log('UI_TEST_PASSED',process.argv[3]||'gpu',await page.locator('#phase').innerText());await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
