function [results,quitProgram] = runVoltageTransientSweep(File,rate_arr,asymRatio_arr)
%RUNVOLTAGETRANSIENTSWEEP Run runVoltageTransient4 across multiple
%stimulation rates and/or W2:W1 phase-width asymmetry ratios.
%
%   [results,quitProgram] = runVoltageTransientSweep(File,rate_arr,asymRatio_arr)
%
%   File           - prepared File struct, as passed into runVoltageTransient4
%                    (Subject, channel mapping, Amplitude1/2, PhaseWidth1,
%                    NumberOfPulses, etc. already set)
%   rate_arr       - vector of stimulation rates to test (pps), e.g. [50 100 200 500]
%   asymRatio_arr  - vector of PhaseWidth2/PhaseWidth1 ratios to test;
%                    1 = symmetric biphasic. Defaults to [1] if omitted.
%
%   For every (rate, ratio) combination, PhaseWidth2 is set to
%   PhaseWidth1*ratio and runVoltageTransient4 rebalances the second-phase
%   amplitude so the pulse stays charge-balanced (Amplitude1*PhaseWidth1 ==
%   Amplitude2*PhaseWidth2) at every ratio. Each run is saved under its own
%   File.Test tag so results from different rates/ratios don't overwrite
%   each other. The sweep stops early if a run sets quitProgram.

%% Variables
if nargin < 3 || isempty(asymRatio_arr)
    asymRatio_arr = 1;
end

phaseWidth1_base = File.Parameters.PhaseWidth1;
test_base = File.Test;
if ~ischar(test_base) && ~(isstring(test_base) && isscalar(test_base))
    test_base = 'VT';
end

numOfRates = length(rate_arr);
numOfRatios = length(asymRatio_arr);
results = struct('Rate',{},'AsymmetryRatio',{},'File',{});
quitProgram = false;

%% Sweep
for rateIdx = 1:numOfRates
    rate = rate_arr(rateIdx);
    for ratioIdx = 1:numOfRatios
        ratio = asymRatio_arr(ratioIdx);
        fprintf('=== Sweep: %g pps, W2:W1 = %g ===\n',rate,ratio);

        File.Parameters.StimulationRate = rate;
        File.Parameters.PhaseWidth2 = phaseWidth1_base * ratio;
        File.Parameters.Symmetry = (ratio == 1);
        File.Test = sprintf('%s_%gpps_asym%gx',test_base,rate,ratio);

        [File,quitProgram] = runVoltageTransient4(File);

        resultIdx = (rateIdx - 1) * numOfRatios + ratioIdx;
        results(resultIdx).Rate = rate;
        results(resultIdx).AsymmetryRatio = ratio;
        results(resultIdx).File = File;

        if quitProgram
            fprintf('Sweep stopped early at %g pps, W2:W1 = %g.\n',rate,ratio);
            return;
        end
    end
end

fprintf('Sweep complete: %d rate(s) x %d ratio(s) = %d run(s).\n', ...
    numOfRates,numOfRatios,numOfRates*numOfRatios);

end
