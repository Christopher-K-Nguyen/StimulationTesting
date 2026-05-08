function [param,ci]  = wblfit2(data_arr,prob_arr)
% Data
len = length(data_arr);
data_sort = sort(data_arr);
data_use = zeros(len,1);
prob_use = zeros(len,1);
data_use(:) = data_sort(:);
prob_use(:) = prob_arr(:);
if length(data_arr) ~= length(prob_arr)
    error('data_arr and prob_arr must have the same length');
end

% Fit Weibull model using Curve Fitting Toolbox
wblModel = fittype('1 - exp(-(x/a)^b)', 'independent', 'x', 'coefficients', {'a', 'b'});

% Fit the Weibull model
f = fit(data_use, prob_use, wblModel, 'StartPoint', [mean(data_sort), 1.5]);

% Extract parameters
scale_a = f.a; % Scale parameter
shape_b = f.b; % Shape parameter
param = [scale_a shape_b];

% Extract confidence interval
ci = confint(f); % Confidence intervals for parameters

end