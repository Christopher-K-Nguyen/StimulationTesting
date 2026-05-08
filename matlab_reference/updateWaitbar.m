function percentage = updateWaitbar(File,varargin)
%% Variables
% Pulsing
try
    expType = File.Test.Experiment;
    isPulsing = contains2(expType,{'SP','LP'});
    if isPulsing
        pulsingTime = File.Test.Duration;
        numOfPulses = File.Test.NumberOfPulses;
        stimRate = File.Parameters.StimulationRate;
        pulsingTime_use = addCommas(pulsingTime);
        numOfPulses_use = addCommas(numOfPulses);

        % Waitbar
        startTime = File.Test.StartTime;
        progBar = File.Test.Progress;
        presentTime = toc(startTime);
        pulsingProg = presentTime / pulsingTime;
        presentTime_use = addCommas(presentTime);
        presentTime_round_use = addCommas(round(presentTime,1));
        if ~contains2(presentTime_round_use,'.')
            presentTime_round_use = [presentTime_round_use '.0'];
        end
        presentPulses = presentTime * stimRate;
        presentPulses_use = addCommas(presentPulses);
        presentPulses_round_use = addCommas(round(presentPulses));

        %% Function
        progTime = sprintf('%s / %s s', ...
            presentTime_round_use,pulsingTime_use);
        progPulse = sprintf('(%s / %s pulses)', ...
            presentPulses_round_use,numOfPulses_use);
        progMsg = {progTime progPulse};
        % if isempty(varargin)
        %     progMsg = prog;
        % else
        %     msg = varargin{1};
        %     progMsg = {prog msg};
        % end
        waitbar(pulsingProg,progBar,progMsg);
        percentage = pulsingProg * 100;
    end
catch
end

end