function varargout = getLinReg(varargin)

numOfVar = length(varargin);
if numOfVar > 1
    x = varargin{1};
    y = varargin{2};
    len = length(x);
else
    y = varargin{1};
    len = length(y);
    x = 1:len;
end
x_arr = zeros(len,1);
y_arr = zeros(len,1);
x_arr(:) = x(:);
y_arr(:) = y(:);

% Linear Regression
linReg = polyfit(x_arr,y_arr,1);
slope = linReg(1);
intercept = linReg(2);
fit_data = polyval(linReg,x_arr);
% ones_arr = ones(len,1);
% X = [ones_arr x_arr];
% b = X \ y_arr;
% slope = b(1);
% intercept = b(2);
% fit_data = X * b;

% Coefficient of Determination
% Sum of Squares
% y_mean = mean(y_data);
% meanDiff = y_data - y_mean;
% squares = meanDiff .^ 2;
% sumOfSquares = sum(squares);   % Total Sum-Of-Squares
numOfValues = length(y_arr);
y_var = var(y);
sumOfSquares = (numOfValues - 1) * y_var;
% Residual sum of squares
resid = y_arr - fit_data;
residSquares = resid .^ 2;
residSumSquares = sum(residSquares); % Residual Sum-Of-Squares
r2 = 1 - residSumSquares/sumOfSquares; 

varargout{1} = slope;
varargout{2} = intercept;
varargout{3} = r2;

end