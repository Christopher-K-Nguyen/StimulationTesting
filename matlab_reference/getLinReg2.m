function [slope,intercept,r2] = getLinReg2(x,y,method)
len = length(x);
x_arr = zeros(len,1);
y_arr = zeros(len,1);
x_arr(:) = x(:);
y_arr(:) = y(:);

% Linear Regression
switch upper(method)
    case {'OLS','MSE'}
        % Ordinary Least Squares (OLS) Linear Regression
        linReg = polyfit(x_arr,y_arr,1);
    case 'MLE'
        % Maximum Likelihood Estimation (MLE) Linear Regression
        % Define negative log-likelihood function
        negLogLikelihood = @(params) ...
            0.5 * len * log(2 * pi * params(3)^2) + ...
            0.5 * sum(((y_arr - (params(1) + params(2) * x_arr)).^2) / params(3)^2);
        
        % Initial guesses for slope, intercept, and sigma
        initialParams = [0; 0; std(y_arr)];
        
        % Minimize negative log-likelihood
        linReg = fminsearch(negLogLikelihood, initialParams);
        % sigma_hat = linReg(3); % Standard deviation of residuals
end
slope = linReg(1);
intercept = linReg(2);
fit_data = polyval(linReg,x_arr);

% Coefficient of Determination
% Sum of Squares
numOfValues = length(y_arr);
y_var = var(y);
sumOfSquares = (numOfValues - 1) * y_var;
% Residual sum of squares
resid = y_arr - fit_data;
residSquares = resid .^ 2;
residSumSquares = sum(residSquares); % Residual Sum-Of-Squares
r2 = 1 - residSumSquares/sumOfSquares;

end