function [n, powerEst] = wblSizePower(a, varargin)
% Estimates sample size needed for detecting a specified effect size in 
% the scale parameter of a Weibull distribution
%
% Inputs:
% - a: Known scale parameter of the Weibull distribution
% - varargin: Optional parameters including EffectSize (desired effect size for scale parameter),
%             Alpha (significance level), Power (desired statistical power), 
%             and Range (range of sample sizes to consider)
%
% Outputs:
% - n: Estimated minimum sample size required to detect the specified effect size
% - powerEst: Power achieved with the estimated sample size

% Set up input parser
p = inputParser;

% Define default values
defaultEffectSize = 0.2; % Default effect size for scale parameter
defaultAlpha = 0.05; % Default significance level
defaultPower = 0.8; % Default desired power
defaultRange = 10:100; % Default range of sample sizes to consider

% Add optional inputs with default values
addParameter(p, 'EffectSize', defaultEffectSize, @isnumeric);
addParameter(p, 'Alpha', defaultAlpha, @isnumeric);
addParameter(p, 'Power', defaultPower, @isnumeric);
addParameter(p, 'Range', defaultRange, @isnumeric);

% Parse the input
parse(p, varargin{:});

% Extract values from inputParser
effectSize = p.Results.EffectSize;
alpha = p.Results.Alpha;
power = p.Results.Power;
range_n = p.Results.Range;

% Initialize output
n = NaN;
powerEst = 0;

% Loop through range of sample sizes to find the required sample size
for n_check = range_n
    % Estimate power for current sample size using the specified effect size
    tmpPower = wblPowerEst(a, effectSize, n_check, alpha);
    
    if tmpPower >= power
        n = n_check;
        powerEst = tmpPower;
        break; % Found required sample size
    end
end

if isnan(n)
    warning('Could not achieve desired power within the specified sample size range.');
end

end

function power = wblPowerEst(a, effectSize, n, alpha)
% Placeholder function for power estimation based on the Weibull distribution.
% This simplified calculation assumes direct proportionality between effect size
% and detectability in a normally distributed statistic. Actual implementation
% may require simulation or analytical methods tailored to the Weibull distribution.

% Simplified calculation for illustrative purposes
z = (effectSize / sqrt((a^2) / n)); % Simplified Z statistic
x = z - norminv(alpha);
power = normcdf(x, 0, 1); % Simplified power calculation
end