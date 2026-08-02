import { expect, test } from '@playwright/test'

test('creates and selects sessions, approves work, streams output, and stops a run', async ({
  page,
}) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '研究对话' })).toBeVisible()

  await page.getByRole('button', { name: '新建会话' }).click()
  const question = page.getByRole('textbox', { name: '研究问题' })
  await question.fill('Compare SQLite and DuckDB')
  await page.getByRole('button', { name: '开始调研' }).click()

  const planDialog = page.getByRole('dialog')
  await expect(planDialog).toContainText('调研计划待确认')
  await expect(planDialog).toContainText('Confirm the research scope')
  await planDialog.getByRole('button', { name: '批准计划' }).click()

  const toolDialog = page.getByRole('dialog')
  await expect(toolDialog).toContainText('http_request')
  await expect(toolDialog).toContainText('http://127.0.0.1:8000/api/health')
  await toolDialog.getByRole('button', { name: '本会话允许' }).click()

  await expect(page.getByText(/Deterministic report complete/).first()).toBeVisible()
  await expect(
    page.getByRole('link', { name: /\[S1\].*Deterministic E2E Source/ }),
  ).toBeVisible()
  await expect(page.getByText('已就绪', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '新建会话' }).click()
  await expect(page.getByRole('button', { name: /未命名调研/ })).toBeVisible()
  await page.getByRole('button', { name: /Compare SQLite and DuckDB/ }).click()
  await expect(page.getByText(/Deterministic report complete/)).toBeVisible()
  await expect(
    page.getByRole('link', { name: /\[S1\].*Deterministic E2E Source/ }),
  ).toBeVisible()

  await page.getByRole('button', { name: /未命名调研/ }).click()
  await question.fill('Wait until stopped')
  await page.getByRole('button', { name: '开始调研' }).click()
  await expect(page.getByRole('dialog')).toContainText('调研计划待确认')
  await page.getByRole('dialog').getByRole('button', { name: '批准计划' }).click()

  const cancelResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' && response.url().endsWith('/cancel'),
  )
  await page.getByRole('button', { name: '停止运行' }).click()
  expect((await cancelResponse).status()).toBe(200)
  await expect(page.getByRole('button', { name: '停止中' })).toBeDisabled()
  await expect(page.getByText('已就绪', { exact: true })).toBeVisible()
})
