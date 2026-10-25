<!--
ARCHITECT REVIEW (T-014, 2026-10-25). Kept as delivered, but NOT a source of
defaults. Every claim is marked UNCERTAIN and no primary source was quoted.
Known error: the "HARD" formula below is the ARD (|A-B| / mean). HARD is half
of that: HARD = 100 * |A-B| / (A+B). Thresholds in the table are unsourced
industry folklore and must be confirmed against primary documents (CIM 2019,
JORC 2012 Table 1, Abzalov 2016 ch. on QA/QC) before MineCore ships them as
defaults. Until then QA/QC limits are user-set, with these values only as
labelled suggestions.
-->
# QA/QC Research & Validation Standards for MineCore

## Summary
This research document defines the core assay QA/QC validation rules, statistical formulas, and default thresholds for MineCore's drillhole validation report. It covers Certified Reference Materials (CRMs), analytical blanks, field/coarse/pulp duplicates, insertion rates, detection limit handling, and alignment with public reporting codes (CIM, JORC, NI 43-101) and mining geology literature (Abzalov, Sinclair & Blackwell). Default thresholds and formulas are established to automate data verification workflows in resource estimation.

---

## 1. Certified Reference Materials (CRMs)
- **Acceptance Rules:** Routine CRM results are evaluated using control charts. Results falling within $\pm 2$ standard deviations (SD) of the certified mean are accepted as pass [Status: UNCERTAIN - primary source not viewed]. Results outside $\pm 3$ SD are classified as failures requiring batch reassay [Status: UNCERTAIN]. A single result between $\pm 2$ SD and $\pm 3$ SD serves as a warning [Status: UNCERTAIN]. Consecutive-sample rules (e.g., Westgard rules such as two consecutive results > 2 SD on the same side, or 10 consecutive results on one side of the mean) indicate analytical bias [Status: UNCERTAIN].
- **Bias Calculation:** 
  $$\text{Bias (\%)} = \frac{X_{\text{measured}} - X_{\text{certified}}}{X_{\text{certified}}} \times 100$$
  where $X_{\text{measured}}$ is the laboratory reported value and $X_{\text{certified}}$ is the certified reference value. Typical acceptable bias is within $\pm 5\%$ [Status: UNCERTAIN].
- **Standard Deviation Selection:** Projects typically use the certificate's "between-lab" standard deviation for initial control limits. However, long-term project-measured standard deviation can be integrated once sufficient sample volume is established [Status: UNCERTAIN].
- **Mislabels and Swaps:** CRM mislabels or sample swaps are detected by abrupt grade discrepancies where a sample's reported value matches a different CRM standard utilized in the same analytical batch [Status: UNCERTAIN].

---

## 2. Blanks
- **Thresholds:** Analytical blanks are evaluated against the lower detection limit (LDL) of the analytical method. A blank exceeding $3\times \text{LDL}$ to $5\times \text{LDL}$ is flagged as a failure or contamination event [Status: UNCERTAIN].
- **Carry-over Contamination:** Detected when a blank sample immediately succeeding a high-grade ore sample exceeds the failure threshold, indicating inadequate cleaning of pulverizers, crushers, or fire assay crucibles [Status: UNCERTAIN].

---

## 3. Duplicates (Field, Coarse, Pulp)
- **HARD (Half Absolute Relative Difference):**
  $$\text{HARD} = \frac{|A - B|}{(A + B) / 2} \times 100$$
  where $A$ and $B$ are the original and duplicate sample grades. 
  *Typical Acceptance:* Pulp duplicates $\ge 90\%$ of pairs within $\pm 10\%$; Coarse rejects $\ge 90\%$ within $\pm 15\%\text{--}20\%$; Field duplicates $\ge 90\%$ within $\pm 30\%$ [Status: UNCERTAIN].
- **Thompson-Howarth Precision:** Evaluates analytical precision as a function of concentration across paired duplicate data using robust linear regression on absolute differences vs. means [Status: UNCERTAIN].
- **RMA (Reduced Major Axis) Regression:** Used to assess proportional and constant bias between original and duplicate pairs, accounting for errors in both variables [Status: UNCERTAIN].
- **Ranked HARD Plot:** Cumulative frequency plot ranking HARD values from 0% to 100% against percentile, used to visually determine precision thresholds (e.g., the 90th percentile HARD value) [Status: UNCERTAIN].

---

## 4. Insertion Rates
- **Expected Rates:** Standard industry practice dictates an insertion rate of approximately 5% for CRMs, 5% for blanks, and 5% for duplicates (split among field, coarse, and pulp), achieving a total QA/QC insertion rate of 15% [Status: UNCERTAIN].
- **Reporting:** Insertion rates and QA/QC batch performance are tracked continuously and summarized in monthly reconciliation reports as well as statutory technical reports (NI 43-101 / JORC) [Status: UNCERTAIN].

---

## 5. Detection-Limit Handling
- **Coding:** Values below the lower detection limit (LDL) are typically coded as $-\text{LDL}$, half-detection limit ($\text{LDL}/2$), or left uncensored for specialized geostatistical treatment [Status: UNCERTAIN].
- **Criticism of Half-DL Substitution:** Arbitrary substitution (such as $\text{LDL}/2$) creates artificial data clustering, distorts variance, and biases spatial variogram modeling and grade estimation [Status: UNCERTAIN]. Modern workflows prefer Maximum Likelihood Estimation (MLE), Regression on Order Statistics (ROS), or treating below-detection data as censored intervals [Status: UNCERTAIN].

---

## 6. Public Guidance Documents & Literature
- **CIM Estimation Best Practice Guidelines (2019):** Outlines requirements for data verification, QA/QC program design, and sample integrity monitoring [Status: UNCERTAIN].
- **JORC Code (2012 Edition):** Table 1, Section 1 ("Sampling techniques and data") specifies reporting requirements for assay data quality, CRM performance, and duplicate precision [Status: UNCERTAIN].
- **NI 43-101 / Form 43-101F1:** Item 11 mandates disclosure of sample preparation, analytical procedures, security measures, and QA/QC results [Status: UNCERTAIN].
- **Abzalov, M. (2016) *Applied Mining Geology*, Springer:** Covers sampling errors, QA/QC data analysis, and control chart methodologies [Status: UNCERTAIN].
- **Sinclair, A.J., & Blackwell, G.H. (2002) *Applied Mineral Inventory Estimation*, Cambridge University Press:** Details data quality assessment, duplicate pair evaluation, and precision plots [Status: UNCERTAIN].

---

## MineCore Default QA/QC Threshold Table

| QA/QC Item | Default Threshold / Acceptance Criteria | Source Reference | Status |
| :--- | :--- | :--- | :--- |
| **CRM Warning Limit** | $\pm 2$ SD from certified mean | Standard Industry Practice | UNCERTAIN |
| **CRM Failure Limit** | $\pm 3$ SD from certified mean | Standard Industry Practice | UNCERTAIN |
| **CRM Bias Limit** | $\pm 5\%$ relative bias | Standard Industry Practice | UNCERTAIN |
| **Blank Contamination Limit** | $> 5 \times$ Lower Detection Limit (LDL) | Standard Industry Practice | UNCERTAIN |
| **Pulp Duplicate HARD** | $\ge 90\%$ of pairs within $\pm 10\%$ HARD | Standard Industry Practice | UNCERTAIN |
| **Coarse Duplicate HARD** | $\ge 90\%$ of pairs within $\pm 15\%$ HARD | Standard Industry Practice | UNCERTAIN |
| **Field Duplicate HARD** | $\ge 90\%$ of pairs within $\pm 30\%$ HARD | Standard Industry Practice | UNCERTAIN |
| **Minimum Insertion Rate** | $5\%$ CRMs, $5\%$ Blanks, $5\%$ Duplicates | Standard Industry Practice | UNCERTAIN |

---

## Open Questions
1. Should MineCore support user-defined custom Westgard rules, or stick to fixed $\pm 2\text{SD} / \pm 3\text{SD}$ limits?
2. What is the preferred default handling method for below-detection-limit assays in MineCore's grade estimation module (Half-DL vs. ROS vs. MLE)?
3. How should batch failure reprocessing results be linked in the database hierarchy?

---

## Full Source List
1. CIM (Canadian Institute of Mining, Metallurgy and Petroleum). (2019). *CIM Estimation Best Practice Guidelines*. [Status: UNCERTAIN]
2. JORC (Joint Ore Reserves Committee). (2012). *Australasian Code for Reporting of Exploration Results, Mineral Resources and Ore Reserves (The JORC Code)*. [Status: UNCERTAIN]
3. CSA International / Canadian Securities Administrators. *NI 43-101 Standards of Disclosure for Mineral Projects*. [Status: UNCERTAIN]
4. Abzalov, M. (2016). *Applied Mining Geology*. Springer. [Status: UNCERTAIN]
5. Sinclair, A.J., & Blackwell, G.H. (2002). *Applied Mineral Inventory Estimation*. Cambridge University Press. [Status: UNCERTAIN]