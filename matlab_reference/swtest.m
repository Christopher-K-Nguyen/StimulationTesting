function [h, pValue, W] = swtest(x, alpha)
    % SWTEST Shapiro-Wilk Test for Normality
    %   [h, pValue, W] = SWTEST(x, alpha) performs the Shapiro-Wilk test for
    %   normality on the data vector x. The null hypothesis is that the data in
    %   x comes from a normal distribution.
    %
    %   Input:
    %   - x: Vector of data
    %   - alpha: Significance level (default: 0.05)
    %
    %   Output:
    %   - h: Hypothesis test result (1 if null hypothesis is rejected, 0 otherwise)
    %   - pValue: P-value of the test
    %   - W: Test statistic
    
    % Set default significance level
    if nargin < 2
        alpha = 0.05;
    end
    
    % Ensure x is a column vector
    x = x(:);
    n = length(x);
    
    if n < 3 || n > 5000
        error('Sample size must be between 3 and 5000 for the Shapiro-Wilk test.');
    end
    
    % Sort the data
    x = sort(x);
    
    % Calculate the mean of the data
    meanX = mean(x);
    
    % Calculate coefficients 'a' using the expected values of order statistics
    m = norminv(((1:n)' - 0.375) / (n + 0.25)); % Expected values of order statistics
    V = m' * m;
    a = m / sqrt(V);
    
    % Calculate the test statistic W
    numerator = (a' * (x - meanX))^2;
    denominator = sum((x - meanX).^2);
    W = numerator / denominator;
    
    % Compute P-value using approximation tables or a known function
    if n <= 50
        % Use precomputed approximation for smaller sample sizes
        % Here we assume you have a function or table to compute p-value
        pValue = shapiroPValueSmallSample(W, n);
    else
        % Approximation for larger sample sizes
        muW = -1.2725 + 1.0521 * log(n);
        sigmaW = 1.0308 - 0.26758 * log(n);
        z = (log(1 - W) - muW) / sigmaW;
        pValue = 2 * normcdf(z, 0, 1); % Two-tailed test
    end
    
    % Test decision
    h = pValue < alpha;
end

function p = shapiroPValueSmallSample(W, n)
    % Placeholder function for small sample p-value approximation.
    % You can use tables or interpolation for accuracy.
    % Replace this with the actual implementation or interpolation.
    p = 0.05; % Default placeholder value
end
