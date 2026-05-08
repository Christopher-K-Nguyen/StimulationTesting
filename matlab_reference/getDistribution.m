function [isNorm,isLognorm,isWeibull] = getDistribution(data,name,varargin)
%% Variables
isNorm = false;
isLognorm = false;
isWeibull = false;

%% Input
isGetNorm = false;
isGetLognorm = false;
isGetWeibull = false;
if ~isempty(varargin)
    str = varargin{1};
    if contains2(str,'log')
        isGetLognorm = true;
    end
    if contains2(str,'norm') && ~contains2(str,'log')
        isGetNorm = true;
    end
    if contains2(str,{'weibull','wbl'})
        isGetWeibull = true;
    end
else
    isGetNorm = true;
    isGetLognorm = true;
    isGetWeibull = true;
end
logData = log(data);
real_tf = isreal(logData);
logData(~real_tf) = [];

%% Tests
% Normal
if isGetNorm
    % test = 'Normal QQ';
    % normQQ = sprintf('%s %s',name,test);
    % figure(101);
    % norm = makedist('Normal');
    % qqplot(data,norm);
    % title(normQQ);
    startTime = tic;
    [isNotNorm_L,pNormL] = lillietest(data,'Distribution','normal');
    [isNotNorm_AD,pNormAD] = adtest(data,'Distribution','norm');
    [isNotNorm_SW,pNormSW] = swtest(data);
    [isNotNorm_KS,pNormKS] = kstest(data);
    [isNotNorm_JB,pNormJB] = jbtest(data);
    doesPassNorm = ~isNotNorm_L + ~isNotNorm_AD + ~isNotNorm_SW + ~isNotNorm_KS + ~isNotNorm_JB;
    if doesPassNorm > 1
        isNorm = true;
        fprintf('%s is in normal distribution\n',name);
    else
        fprintf('%s is NOT in normal distribution\n',name);
    end

    fprintf('\t');
    if isNotNorm_L
        fprintf('*');
    end
    fprintf('Lilliefors normal test, p = %f\n',pNormL);

    fprintf('\t');
    if isNotNorm_AD
        fprintf('*');
    end
    fprintf('Anderson-Darling normal test, p = %f\n',pNormAD);

    fprintf('\t');
    if isNotNorm_SW
        fprintf('*');
    end
    fprintf('Shapiro-Wilk normal test, p = %f\n',pNormSW);
    
    % fprintf('\t');
    % if isNotNorm_KS
    %     fprintf('*');
    % end
    % fprintf('One-sample Kolmogorov-Smirnov normal test., p = %f\n',pNormKS);
    % 
    fprintf('\t');
    if isNotNorm_JB
        fprintf('*');
    end
    fprintf('Jarque-Bera normal test, p = %f\n',pNormJB);

    [endTime,unit] = getEndTime(startTime);
    fprintf('Time Elapsed: %.2f %s\n',endTime,unit);
end

% Lognormal
if isGetLognorm
    % test = 'Lognormal QQ';
    % lognormQQ = sprintf('%s %s',name,test);
    % figure(102);
    % lognorm = makedist('Lognormal');
    % qqplot(data,lognorm);
    % title(lognormQQ);
    startTime = tic;
    try
        [isNotLognorm_L,pLognorm_L] = lillietest(logData,'Distribution','normal');
    catch
        isNotLognorm_L = 1;
        pLognorm_L = 0;
    end
    [isNotLognorm_AD,pLognorm_AD] = adtest(data,'Distribution','logn');
    try
        [isNotLogorm_SW,pLogormSW] = swtest(logData);
    catch
        isNotLogorm_SW = 1;
        pLogormSW = 0;
    end
    try
        [isNotLognorm_KS,pLognorm_KS] = kstest(logData);
    catch
        isNotLognorm_KS = 1;
        pLognorm_KS = 0;
    end
    try
        [isNotLognorm_JB,pLognorm_JB] = jbtest(logData);
    catch
        isNotLognorm_JB = 1;
        pLognorm_JB = 0;
    end
    doesPassLognorm = ~isNotLognorm_L + ~isNotLognorm_AD + ~isNotLogorm_SW + ~isNotLognorm_KS + ~isNotLognorm_JB;
    if doesPassLognorm > 1
        isLognorm = true;
        fprintf('%s is in lognormal distribution\n',name);
    else
        fprintf('%s is NOT in lognormal distribution\n',name);
    end

    fprintf('\t');
    if isNotLognorm_L
        fprintf('*');
    end
    fprintf('Lilliefors lognormal test, p = %f\n',pLognorm_L);

    fprintf('\t');
    if isNotLognorm_AD
        fprintf('*');
    end
    fprintf('Anderson-Darling lognormal test, p = %f\n',pLognorm_AD);

    fprintf('\t');
    if isNotLogorm_SW
        fprintf('*');
    end
    fprintf('Shapiro-Wilk normal test, p = %f\n',pLogormSW);

    % fprintf('\t');
    % if isNotLognorm_KS
    %     fprintf('*');
    % end
    % fprintf('One-sample Kolmogorov-Smirnov lognormal test, p = %f\n',name,pLognorm_KS);

    fprintf('\t');
    if isNotLognorm_JB
        fprintf('*');
    end
    fprintf('Jarque-Bera lognormal test,  p = %f\n',pLognorm_JB);

    [endTime,unit] = getEndTime(startTime);
    fprintf('Time Elapsed: %.2f %s\n',endTime,unit);
end

% Weibull
if isGetWeibull
    % test = 'Weibull QQ';
    % wbQQ = sprintf('%s %s',name,test);
    % figure(103);
    % wb = makedist('Weibull');
    % qqplot(data,wb);
    % title(wbQQ);
    startTime = tic;
    try
        [isNotWeibull_L,pWeibull_L] = lillietest(logData,'Distribution','extreme value');
    catch
        isNotWeibull_L = 1;
        pWeibull_L = 0;
    end
    [isNotWeibull_AD,pWeibull_AD] = adtest(data,'Distribution','weibull');
    doesPassWeibull = ~isNotWeibull_L + ~isNotWeibull_AD;
    if doesPassWeibull > 1
        isWeibull = true;
        fprintf('%s is in Weibull distribution\n',name);
    else
        fprintf('%s is NOT in Weibull distribution\n',name);
    end

    fprintf('\t');
    if isNotWeibull_L
        fprintf('*');
    end
    fprintf('Lilliefors Weibull test, p = %f\n',pWeibull_L);

    fprintf('\t');
    if isNotWeibull_AD
        fprintf('*');
    end
    fprintf('Anderson-Darling Weibull test, p = %f\n',pWeibull_AD);
    
    [endTime,unit] = getEndTime(startTime);
    fprintf('Time Elapsed: %.2f %s\n',endTime,unit);
end

end