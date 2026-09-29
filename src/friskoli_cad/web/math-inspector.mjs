import katex from './vendor/katex/katex.mjs';

const element = (tag, className, text = '') => {
  const item = document.createElement(tag); item.className = className; item.textContent = text; return item;
};
export function renderModuleDocumentation(parent, manifest, t) {
  const math = manifest.declaration?.mathematics;
  if (!math) return;
  const panel = element('section','property-section module-mathematics');
  panel.append(element('h3','',t('mathematics')));
  if (math.kind === 'undocumented') panel.append(element('p','empty-message',t('mathPending')));
  for (const equation of math.equations) {
    const rendered = element('div','module-equation');
    katex.render(equation.latex, rendered, {displayMode:true,trust:false,throwOnError:false,
      strict:'error',maxExpand:1000,maxSize:10});
    panel.append(rendered);
    const details = element('details','equation-source'); details.append(element('summary','',t('symbolsAndSource')));
    details.append(element('pre','',equation.latex));
    for (const [symbol,meaning] of Object.entries(equation.symbols)) {
      const row = element('div','symbol-row'); row.append(element('code','',symbol),element('span','',meaning)); details.append(row);
    }
    panel.append(details);
  }
  if (math.algorithm) panel.append(element('p','math-description',math.algorithm));
  for (const note of math.assumptions) panel.append(element('p','empty-message',note));
  const evidence = element('details','equation-source'); evidence.append(element('summary','',t('implementationEvidence')));
  evidence.append(element('code','implementation-path',math.implementation));
  evidence.append(element('p','empty-message',t(math.verification.status === 'tested' ? 'registeredTests' : 'mathUnreviewed')));
  for (const reference of math.verification.tests) evidence.append(element('code','implementation-path',reference));
  panel.append(evidence); parent.append(panel);
}
